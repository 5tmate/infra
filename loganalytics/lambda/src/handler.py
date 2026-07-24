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
SOURCE_PREFIX = "AWSLogs/227469555418/us-east-1/_aws_lambda_langflow/"
FINDINGS_KEY = "findings.parquet"
STATE_KEY = "state.json"

GRAIN = ["client_ip", "path", "body", "category", "code_callback", "oast_callback"]


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
        "CREATE OR REPLACE TEMP TABLE new_grain (client_ip VARCHAR, path VARCHAR, body VARCHAR, "
        "category VARCHAR, code_callback BOOLEAN, oast_callback BOOLEAN)"
    )
    con.executemany(
        "INSERT INTO new_grain VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                f["client_ip"],
                f["path"],
                f["body"],
                f["category"],
                bool(f["code_callback"]),
                bool(f["oast_callback"]),
            )
            for f in findings
        ],
    )
    cols = ", ".join(GRAIN)
    source = f"SELECT {cols} FROM new_grain"
    if existing_path:
        source = f"SELECT {cols} FROM read_parquet('{existing_path}') UNION ALL {source}"
    con.execute(f"COPY (SELECT DISTINCT {cols} FROM ({source})) TO '{out_path}' (FORMAT parquet)")
    return con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]


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
    summary = {
        "first_run": watermark is None,
        "watermark": watermark.isoformat() if watermark else None,
        "objects": len(keys),
        "records": records,
        "rows": rows,
        "skipped": skipped,
        "new_findings": len(findings),
        "total_findings": total,
    }
    print(json.dumps(summary))
    return summary
