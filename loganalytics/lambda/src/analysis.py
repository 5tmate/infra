import ast
import re

CODE_INDICATORS = [
    r"socket",
    r"\.connect\(",
    r"create_connection",
    r"TCPClient",
    r"requests\.get",
    r"requests\.post",
    r"urlopen",
    r"http\.client",
    r"subprocess",
    r"Popen",
    r"os\.system",
    r"os\.popen",
    r"\bcurl\b",
    r"\bwget\b",
    r"Invoke-WebRequest",
    r"DownloadString",
    r"bash\s+-i",
    r"sh\s+-i",
    r"/dev/tcp/",
    r"\bnc\b",
    r"\bncat\b",
    r"\bsocat\b",
    r"powershell",
    r"Invoke-Expression",
    r"\bIEX\b",
    r"Net\.Sockets\.TCPClient",
    r"https?://",
    r"\d{1,3}(?:\.\d{1,3}){3}:\d+",
    r"base64",
    r"b64decode",
    r"\bexec\b",
    r"\beval\b",
    r"__import__",
]
CALLBACK_PATTERN = re.compile("|".join(CODE_INDICATORS), re.IGNORECASE)

OAST_RE = (
    r"[a-z0-9.-]+\.(interactsh\.com|oast\.(fun|site|pro|live|me)|oastify\.com|canarytokens\.com)"
)

CLASSIFY = """
CREATE OR REPLACE TEMP TABLE classified AS
WITH base AS (
    SELECT *, CASE WHEN json_valid(body) THEN body ELSE '{}' END AS sb
    FROM requests
)
SELECT
    * EXCLUDE (sb),
    CASE
        WHEN method = 'POST' AND path = '/api/v1/validate/code' THEN 'cve_2025_3248'
        WHEN method = 'POST' AND path LIKE '/api/v1/build_public_tmp/%/flow'
            AND json_type(sb, '$.data') IS NOT NULL THEN 'cve_2026_33017'
    END AS category,
    json_extract_string(sb, '$.code') AS code,
    regexp_matches(coalesce(body, ''), ?, 'i') AS oast_callback
FROM base
"""

FINDINGS = """
SELECT ts, client_ip, method, path, category, code, oast_callback, s3_key
FROM classified
WHERE category IS NOT NULL
ORDER BY ts
"""

FINDING_COLUMNS = [
    "ts",
    "client_ip",
    "method",
    "path",
    "category",
    "code",
    "oast_callback",
    "s3_key",
]


def _def_time_exprs(tree):
    """Yield the expressions Python evaluates when a `def` is exec'd: decorators
    and argument defaults. The function body is not among them."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from node.decorator_list
            args = node.args
            yield from (d for d in args.defaults + args.kw_defaults if d is not None)


def has_def_time_callback(code):
    if not code:
        return False
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    return any(CALLBACK_PATTERN.search(ast.unparse(expr)) for expr in _def_time_exprs(tree))


def classify(con):
    con.execute(CLASSIFY, [OAST_RE])


def run(con):
    classify(con)
    findings = [dict(zip(FINDING_COLUMNS, row)) for row in con.execute(FINDINGS).fetchall()]
    for finding in findings:
        finding["code_callback"] = finding["category"] == "cve_2025_3248" and has_def_time_callback(
            finding["code"]
        )
    return findings
