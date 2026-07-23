import json
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
    for key in keys:
        text = ingest.read_body(key, s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        object_rows, object_records, object_skipped = ingest.parse_object(text, key)
        ingest.load(con, object_rows)
        rows += len(object_rows)
        records += object_records
        skipped += object_skipped

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
