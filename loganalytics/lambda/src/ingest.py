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

RAW_COLUMNS = (
    "{'accountId': 'VARCHAR', 'logGroup': 'VARCHAR', 'logStream': 'VARCHAR', "
    "'id': 'VARCHAR', 'timestamp': 'BIGINT', 'message': 'VARCHAR'}"
)


INSERT = """
INSERT INTO requests
SELECT
    make_timestamp("timestamp" * 1000),
    json_extract_string(message, '$.method'),
    json_extract_string(message, '$.path'),
    json_extract_string(message, '$.query'),
    json_extract_string(message, '$.client_ip'),
    json_extract_string(message, '$.ip'),
    CAST(json_extract(message, '$.status') AS INTEGER),
    CAST(json_extract(message, '$.headers') AS VARCHAR),
    json_extract_string(message, '$.body'),
    CAST(json_extract(message, '$.body_bytes') AS BIGINT),
    CAST(json_extract(message, '$.body_truncated') AS BOOLEAN),
    ?
FROM raw WHERE message LIKE '{%' AND json_valid(message)
"""


def create_table(con):
    con.execute(DDL)


def load_file(con, path, s3_key):
    escaped = path.replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE raw AS "
        f"SELECT * FROM read_json('{escaped}', format='newline_delimited', "
        f"compression='zstd', columns={RAW_COLUMNS})"
    )
    records, skipped = con.execute(
        "SELECT count(*), "
        "count(*) FILTER (WHERE message LIKE '{%' AND NOT json_valid(message)) FROM raw"
    ).fetchone()
    rows = con.execute(INSERT, [s3_key]).fetchone()[0]
    return records, rows, skipped
