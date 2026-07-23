import gzip
import json
from datetime import datetime, timezone

KNOWN_KEYS = {"accountId", "logGroup", "logStream", "id", "timestamp", "message"}

DDL = """
CREATE TABLE requests (
    ts TIMESTAMP,
    method VARCHAR,
    path VARCHAR,
    query VARCHAR,
    client_ip VARCHAR,
    ip VARCHAR,
    status INTEGER,
    headers VARCHAR,
    body VARCHAR,
    body_bytes BIGINT,
    body_truncated BOOLEAN,
    s3_key VARCHAR
)
"""


def read_body(key, body):
    if key.endswith(".gz"):
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def split_records(text):
    records, current = [], {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
                current = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep and key.strip() in KNOWN_KEYS:
            current[key.strip()] = value
    if current:
        records.append(current)
    return records


def parse_record(record, s3_key):
    message = record.get("message", "")
    if not message.lstrip().startswith("{"):
        return None
    data = json.loads(message)
    ts = None
    if record.get("timestamp", "").isdigit():
        ms = int(record["timestamp"])
        ts = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    return (
        ts,
        data.get("method"),
        data.get("path"),
        data.get("query"),
        data.get("client_ip"),
        data.get("ip"),
        data.get("status"),
        json.dumps(data.get("headers", {}), ensure_ascii=False),
        data.get("body"),
        data.get("body_bytes"),
        data.get("body_truncated"),
        s3_key,
    )


def parse_object(text, s3_key):
    rows, records, skipped = [], 0, 0
    for record in split_records(text):
        records += 1
        try:
            row = parse_record(record, s3_key)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if row is not None:
            rows.append(row)
    return rows, records, skipped


def create_table(con):
    con.execute(DDL)


def load(con, rows):
    if rows:
        con.executemany("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
