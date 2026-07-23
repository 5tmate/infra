import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import boto3
import duckdb

import analysis
import ingest

BUCKET = "5tmate-langflow-logs"


def select_keys(objects, now, scope):
    if scope == "all":
        return [key for key, _ in objects]
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    return [key for key, modified in objects if start <= modified < end]


def handler(event, context):
    scope = (event or {}).get("scope", "hour")
    s3 = boto3.client("s3")
    objects = [
        (obj["Key"], obj["LastModified"])
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET)
        for obj in page.get("Contents", [])
    ]
    keys = select_keys(objects, datetime.now(timezone.utc), scope)

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
