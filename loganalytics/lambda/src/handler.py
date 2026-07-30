import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import boto3
import duckdb
from botocore.exceptions import ClientError

import analysis
import ingest

BUCKET = "5tmate-langflow-logs"
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "5tmate-loganalytics")
TOPIC_ARN = os.environ.get("TOPIC_ARN")
SOURCE_PREFIX = "AWSLogs/227469555418/us-east-1/_aws_lambda_langflow/"
FINDINGS_KEY = "findings.parquet"
STATE_KEY = "state.json"

ALERT_MAX = 100

GRAIN = ["client_ip", "method", "path", "body", "category"]


def _missing(error):
    return error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404")


def scan_prefixes(now_hour, watermark):
    if watermark is None:
        return [SOURCE_PREFIX]
    prefixes = []
    hour = watermark
    while hour < now_hour:
        prefixes.append(SOURCE_PREFIX + hour.strftime("%Y/%m/%d/%H/"))
        hour += timedelta(hours=1)
    return prefixes


def read_watermark(s3):
    try:
        obj = s3.get_object(Bucket=OUTPUT_BUCKET, Key=STATE_KEY)
    except ClientError as error:
        if _missing(error):
            return None
        raise
    value = json.loads(obj["Body"].read()).get("last_scan_hour")
    return datetime.fromisoformat(value) if value else None


def write_state(s3, now_hour, summary):
    s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=STATE_KEY,
        Body=json.dumps({"last_scan_hour": now_hour.isoformat(), **summary}).encode("utf-8"),
        ContentType="application/json",
    )


def write_deduped(con, findings, existing_path, out_path):
    con.execute(
        "CREATE OR REPLACE TEMP TABLE new_grain (client_ip VARCHAR, method VARCHAR, path VARCHAR, "
        "body VARCHAR, category VARCHAR, last_seen TIMESTAMP)"
    )
    con.executemany(
        "INSERT INTO new_grain VALUES (?, ?, ?, ?, ?, ?)",
        [
            (f["client_ip"], f["method"], f["path"], f["body"], f["category"], f["ts"])
            for f in findings
        ],
    )
    cols = ", ".join(GRAIN)
    seen_cols = f"{cols}, last_seen"
    source = f"SELECT {seen_cols} FROM new_grain"
    if existing_path:
        source = f"SELECT {seen_cols} FROM read_parquet('{existing_path}') UNION ALL {source}"
    con.execute(
        f"COPY (SELECT {cols}, max(last_seen) AS last_seen "
        f"FROM ({source}) GROUP BY {cols}) TO '{out_path}' (FORMAT parquet)"
    )
    return con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]


def _alert_body(cve, window_start, window_end):
    lines = [
        f"- {f['category']}  {f['client_ip']}  {f['method']} {f['path']}\n"
        f"    {(f['body'] or '')[:200]}"
        for f in cve[:ALERT_MAX]
    ]
    if len(cve) > ALERT_MAX:
        lines.append(f"... and {len(cve) - ALERT_MAX} more")
    header = (
        f"{len(cve)} CVE attack request(s) in "
        f"[{window_start.isoformat()}, {window_end.isoformat()})"
    )
    return header + "\n\n" + "\n".join(lines)


def alert_on_cve(watermark, findings, now_hour):

    if watermark is None or not TOPIC_ARN:
        return 0
    seen, cve = set(), []
    for f in findings:
        if not f["category"]:
            continue
        key = tuple(f[c] for c in GRAIN)
        if key not in seen:
            seen.add(key)
            cve.append(f)
    if not cve:
        return 0
    try:
        boto3.client("sns").publish(
            TopicArn=TOPIC_ARN,
            Subject=f"[5tmate] {len(cve)} CVE attack(s) detected"[:100],
            Message=_alert_body(cve, watermark, now_hour),
        )
    except Exception as error:
        print(json.dumps({"alert_error": str(error)}))
        return 0
    return len(cve)


def handler(event, context):
    now = datetime.now(timezone.utc)
    now_hour = now.replace(minute=0, second=0, microsecond=0)
    s3 = boto3.client("s3")

    watermark = read_watermark(s3)
    keys = [
        obj["Key"]
        for prefix in scan_prefixes(now_hour, watermark)
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".zst")
    ]

    con = duckdb.connect()
    ingest.create_table(con)
    records = rows = skipped = 0
    total = None
    with tempfile.TemporaryDirectory() as tmp:
        for i, key in enumerate(keys):
            path = os.path.join(tmp, f"{i:06d}-{os.path.basename(key)}")
            s3.download_file(BUCKET, key, path)
            file_records, file_rows, file_skipped = ingest.load_file(con, path, key)
            records += file_records
            rows += file_rows
            skipped += file_skipped
            os.remove(path)

        findings = analysis.run(con)
        if findings:
            existing = os.path.join(tmp, "existing.parquet")
            try:
                s3.download_file(OUTPUT_BUCKET, FINDINGS_KEY, existing)
            except ClientError as error:
                if not _missing(error):
                    raise
                existing = None
            out = os.path.join(tmp, "findings.parquet")
            total = write_deduped(con, findings, existing, out)
            s3.upload_file(out, OUTPUT_BUCKET, FINDINGS_KEY)

    write_state(
        s3,
        now_hour,
        {
            "scanned_at": now.isoformat(),
            "objects": len(keys),
            "new_findings": len(findings),
            "total_findings": total,
        },
    )
    alerted = alert_on_cve(watermark, findings, now_hour)

    summary = {
        "first_run": watermark is None,
        "watermark": watermark.isoformat() if watermark else None,
        "objects": len(keys),
        "records": records,
        "rows": rows,
        "skipped": skipped,
        "new_findings": len(findings),
        "total_findings": total,
        "cve_alerted": alerted,
    }
    print(json.dumps(summary))
    return summary
