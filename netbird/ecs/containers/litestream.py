from .common import health_check

DATABASES = ["store", "idp", "events"]
LITESTREAM_PROBE = """
m=$(timeout -k 1 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/9090 \\
  && printf "GET /metrics HTTP/1.0\\r\\n\\r\\n" >&3 && cat <&3' 2>/dev/null) \\
  || { echo "metrics unreachable"; return 1; }
cur=$(awk '/^litestream_sync_count[{ ]/ {s += $NF}
  /^litestream_sync_error_count[{ ]/ {e += $NF}
  /^litestream_disk_full[{ ]/ {f += $NF}
  /^litestream_txid[{ ]/ {t += $NF}
  /^litestream_replica_operation_total[{].*operation="PUT"/ {p += $NF}
  END {printf "%.0f %.0f %.0f %.0f %.0f", s, e, f, t, p}' <<<"$m")
prev=$(cat /tmp/health 2>/dev/null)
echo "$cur" > /tmp/health
[ -n "$prev" ] || return 0
read -r s e f t p <<<"$cur"
read -r ps pe _ pt pp <<<"$prev"
[ "$f" = 0 ] || { echo "disk full"; return 1; }
[ "$e" = "$pe" ] || { echo "sync errors increased"; return 1; }
[ "$s" -gt "$ps" ] || { echo "sync stuck"; return 1; }
[ "$t" = "$pt" ] || [ "$p" -gt "$pp" ] || { echo "new data not uploaded"; return 1; }
"""

_MOUNTS = [
    {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
    {
        "sourceVolume": "litestream-config",
        "containerPath": "/etc/litestream.yml",
        "readOnly": True,
    },
]


def restores(*, image, logs):
    return [
        {
            "name": f"restore-{db}",
            "image": image,
            "essential": False,
            "memoryReservation": 64,
            "command": [
                "restore",
                "-config",
                "/etc/litestream.yml",
                "-if-replica-exists",
                "-force",
                "-integrity-check",
                "full",
                f"/var/lib/netbird/{db}.db",
            ],
            "mountPoints": _MOUNTS,
            "logConfiguration": logs("restore"),
        }
        for db in DATABASES
    ]


def replicate(*, image, logs):
    return {
        "name": "litestream",
        "image": image,
        "essential": False,
        "memoryReservation": 128,
        "entryPoint": ["bash", "-c"],
        "command": [
            "cp /etc/litestream.yml /tmp/litestream.yml"
            " && echo 'addr: 127.0.0.1:9090' >> /tmp/litestream.yml"
            " && exec litestream replicate -config /tmp/litestream.yml"
        ],
        "healthCheck": health_check(
            "bash", LITESTREAM_PROBE, interval=10, retries=3, start_period=60
        ),
        "restartPolicy": {"enabled": True, "restartAttemptPeriod": 300},
        "stopTimeout": 15,
        "dependsOn": [
            {"containerName": f"restore-{db}", "condition": "SUCCESS"} for db in DATABASES
        ],
        "mountPoints": _MOUNTS,
        "logConfiguration": logs("litestream"),
    }
