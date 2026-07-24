import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import boto3
import duckdb

import analysis
import ingest

BUCKET = "5tmate-langflow-logs"
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "5tmate-loganalytics")

SOURCE_PREFIX = "AWSLogs/227469555418/us-east-1/_aws_lambda_langflow/"

RECORD_FIELDS = [
    "ts",
    "client_ip",
    "method",
    "path",
    "category",
    "code_callback",
    "oast_callback",
    "body",
    "s3_key",
]


def source_prefix(now, scope):
    if scope == "all":
        return SOURCE_PREFIX
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return SOURCE_PREFIX + f"{start:%Y/%m/%d/%H}/"


def findings_key(now, scope):
    if scope == "all":
        return f"findings/all/{now:%Y%m%dT%H%M%SZ}.jsonl"
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return f"findings/{start:%Y/%m/%d/%H}.jsonl"


def _record(finding):
    record = {field: finding.get(field) for field in RECORD_FIELDS}
    ts = record["ts"]
    record["ts"] = ts.isoformat() if ts else None
    record["oast_callback"] = bool(record["oast_callback"])
    return record


def handler(event, context):
    scope = (event or {}).get("scope", "hour")
    now = datetime.now(timezone.utc)
    s3 = boto3.client("s3")
    keys = [
        obj["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=source_prefix(now, scope)
        )
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".zst")
    ]

    con = duckdb.connect()
    ingest.create_table(con)
    records = rows = skipped = 0
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
        body = "".join(json.dumps(_record(f)) + "\n" for f in findings)
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=findings_key(now, scope),
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )

    summary = {
        "scope": scope,
        "objects": len(keys),
        "records": records,
        "rows": rows,
        "skipped": skipped,
        "findings": len(findings),
    }
    print(json.dumps(summary))
    return summary
