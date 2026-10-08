DATABASES = ["store", "idp", "events"]
DASHBOARD_HEALTH_SERVER = (
    "server { listen 127.0.0.1:8080; access_log off; root /usr/share/nginx/html; }"
)
DASHBOARD_PROBE = "curl -m 3 -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/"
SERVER_PROBE = """
req='GET /oauth2/.well-known/openid-configuration HTTP/1.0\\r\\nHost: __SERVER__\\r\\n\\r\\n'
line=$(printf '%b' "$req" | timeout -k 1 3 openssl s_client -quiet -connect 127.0.0.1:443 \\
  -servername __SERVER__ 2>/dev/null | head -1 | tr -d '\\r')
echo "${line:-no response}"
case "$line" in *" 200 "*) return 0 ;; esac
return 1
"""
SERVER_HOLD = 75
SERVER_ENTRY = """
probe() {
__PROBE__
}
stopping=0
trap 'stopping=1' TERM
/go/bin/netbird-server --config /etc/netbird/config.yaml &
server=$!
timer=
status=
next() {
  finished=
  wait -n -p finished "$@"
  code=$?
  if [ "$finished" = "$server" ]; then
    status=$code
  elif [ -n "$finished" ] && [ "$finished" = "$timer" ]; then
    timer=
  elif [ -z "$finished" ] && [ "$code" = 127 ]; then
    status=${status:-127}
  fi
}
while [ -z "$status" ] && [ "$stopping" = 0 ]; do next "$server"; done
if [ -z "$status" ] && probe > /dev/null; then
  echo "stop requested while healthy, serving __HOLD__s more before stopping"
  sleep __HOLD__ &
  timer=$!
  while [ -z "$status" ] && [ -n "$timer" ]; do next "$server" "$timer"; done
  [ -n "$timer" ] && kill "$timer" 2> /dev/null
fi
[ -z "$status" ] && kill -TERM "$server" 2> /dev/null
while [ -z "$status" ]; do next "$server"; done
exit "$status"
"""
STUN_PROBE = """
wget -q -T 3 -O /dev/null http://127.0.0.1:9000/health && return 0
echo "relay health endpoint failed"
return 1
"""
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
HEALTH_WRAPPER = """
probe() {
__PROBE__
}
cause() {
  avail=$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)
  total=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
  oom=$(awk '/^oom_kill / {print $2}' /sys/fs/cgroup/memory.events 2>/dev/null)
  load=$(cut -d ' ' -f 1 /proc/loadavg)
  cpus=$(grep -c ^processor /proc/cpuinfo)
  disk=$(df -P / | awk 'NR == 2 {print int($5)}')
  if [ "$avail" -lt $((total / 10)) ] || [ "${oom:-0}" -gt 0 ]; then c=memory
  elif awk -v l="$load" -v n="$cpus" 'BEGIN {exit !(l > n)}'; then c=cpu
  elif [ "$disk" -ge 95 ]; then c=disk
  else c=program
  fi
  echo "cause=$c (memory available $avail/$total MiB, oom kills ${oom:-0}," \\
    "load $load on $cpus cpus, disk $disk%)"
}
say() { echo "HEALTHCHECK $*" > /proc/1/fd/1; }
now=$(date +%s)
age=$(awk -v u="$(cut -d ' ' -f 1 /proc/uptime)" '{print int(u - $22 / 100)}' /proc/1/stat)
n=$(cat /tmp/hc-fails 2>/dev/null || echo 0)
if why=$(probe); then
  if [ "$n" -ge __RETRIES__ ]; then
    red=$(cat /tmp/hc-red 2>/dev/null || echo "$now")
    say "recovered after $((now - red))s"
  fi
  echo 0 > /tmp/hc-fails
  exit 0
fi
[ "$age" -ge __START__ ] || exit 1
n=$((n + 1))
echo "$n" > /tmp/hc-fails
if [ "$n" -eq __RETRIES__ ]; then
  echo "$now" > /tmp/hc-red
  echo "$now" > /tmp/hc-said
  say "unhealthy ${why:+($why) }$(cause)"
  __ON_UNHEALTHY__
elif [ "$n" -gt __RETRIES__ ] && [ $((now - $(cat /tmp/hc-said))) -ge 300 ]; then
  echo "$now" > /tmp/hc-said
  red=$(cat /tmp/hc-red)
  say "still unhealthy for $(( (now - red) / 60 ))m ${why:+($why) }$(cause)"
fi
exit 1
"""


def health_check(shell, probe, *, interval, retries, start_period, on_unhealthy=""):
    script = (
        HEALTH_WRAPPER.replace("__PROBE__", probe.strip())
        .replace("__RETRIES__", str(retries))
        .replace("__START__", str(start_period))
        .replace("__ON_UNHEALTHY__", on_unhealthy)
    )
    return {
        "command": ["CMD", shell, "-c", script],
        "interval": interval,
        "timeout": 5,
        "retries": retries,
        "startPeriod": start_period,
    }


def container_definitions(
    *,
    log_group,
    region,
    management_url,
    idp_url,
    config_s3_uri,
    config_sha256,
    litestream_image,
    dashboard_image,
    server_image,
    stun_image,
    aws_cli_image,
    server_name,
):
    def logs(stream):
        return {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": region,
                "awslogs-stream-prefix": stream,
            },
        }

    litestream_mounts = [
        {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
        {
            "sourceVolume": "litestream-config",
            "containerPath": "/etc/litestream.yml",
            "readOnly": True,
        },
    ]
    server_probe = SERVER_PROBE.replace("__SERVER__", server_name)

    return [
        {
            "name": "dashboard",
            "image": dashboard_image,
            "essential": True,
            "memoryReservation": 128,
            "portMappings": [{"containerPort": 80, "hostPort": 80, "protocol": "tcp"}],
            "environment": [
                {"name": "NETBIRD_MGMT_API_ENDPOINT", "value": management_url},
                {"name": "NETBIRD_MGMT_GRPC_API_ENDPOINT", "value": management_url},
                {"name": "AUTH_AUDIENCE", "value": "netbird-dashboard"},
                {"name": "AUTH_CLIENT_ID", "value": "netbird-dashboard"},
                {"name": "AUTH_CLIENT_SECRET", "value": ""},
                {"name": "AUTH_AUTHORITY", "value": idp_url},
                {"name": "USE_AUTH0", "value": "false"},
                {
                    "name": "AUTH_SUPPORTED_SCOPES",
                    "value": "openid profile email groups",
                },
                {"name": "AUTH_REDIRECT_URI", "value": "/nb-auth"},
                {"name": "AUTH_SILENT_REDIRECT_URI", "value": "/nb-silent-auth"},
                {"name": "NGINX_SSL_PORT", "value": "443"},
                {"name": "LETSENCRYPT_DOMAIN", "value": "none"},
            ],
            "entryPoint": ["sh", "-c"],
            "command": [
                f"echo '{DASHBOARD_HEALTH_SERVER}' > /etc/nginx/http.d/health.conf"
                " && exec /usr/bin/supervisord -c /etc/supervisord.conf"
            ],
            "healthCheck": health_check(
                "sh", DASHBOARD_PROBE, interval=5, retries=2, start_period=30
            ),
            "stopTimeout": 15,
            "logConfiguration": logs("dashboard"),
        },
        *[
            {
                "name": f"restore-{db}",
                "image": litestream_image,
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
                "mountPoints": litestream_mounts,
                "logConfiguration": logs("restore"),
            }
            for db in DATABASES
        ],
        {
            "name": "litestream",
            "image": litestream_image,
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
            "mountPoints": litestream_mounts,
            "logConfiguration": logs("litestream"),
        },
        {
            "name": "config",
            "image": aws_cli_image,
            "essential": False,
            "memoryReservation": 64,
            "command": ["s3", "cp", config_s3_uri, "/etc/netbird/config.yaml"],
            "environment": [
                {"name": "AWS_REGION", "value": region},
                {"name": "CONFIG_SHA256", "value": config_sha256},
            ],
            "mountPoints": [{"sourceVolume": "netbird-config", "containerPath": "/etc/netbird"}],
            "logConfiguration": logs("config"),
        },
        {
            "name": "netbird-server",
            "image": server_image,
            "essential": True,
            "memoryReservation": 768,
            "entryPoint": ["bash", "-c"],
            "command": [
                SERVER_ENTRY.replace("__PROBE__", server_probe.strip()).replace(
                    "__HOLD__", str(SERVER_HOLD)
                )
            ],
            "stopTimeout": SERVER_HOLD + 7,
            "environment": [{"name": "AWS_REGION", "value": region}],
            "dependsOn": [
                {"containerName": "litestream", "condition": "START"},
                {"containerName": "config", "condition": "SUCCESS"},
                {"containerName": "dashboard", "condition": "START"},
                {"containerName": "stun", "condition": "START"},
            ],
            "portMappings": [{"containerPort": 443, "hostPort": 443, "protocol": "tcp"}],
            "healthCheck": health_check(
                "bash",
                server_probe,
                interval=5,
                retries=2,
                start_period=300,
            ),
            "mountPoints": [
                {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
                {
                    "sourceVolume": "netbird-config",
                    "containerPath": "/etc/netbird",
                    "readOnly": True,
                },
            ],
            "logConfiguration": logs("server"),
        },
        {
            "name": "stun",
            "image": stun_image,
            "essential": False,
            "memoryReservation": 64,
            "environment": [
                {"name": "NB_ENABLE_STUN", "value": "true"},
                {"name": "NB_STUN_PORTS", "value": "3478"},
                {"name": "NB_LISTEN_ADDRESS", "value": "127.0.0.1:33080"},
                {"name": "NB_EXPOSED_ADDRESS", "value": "rel://127.0.0.1:33080"},
                {"name": "NB_AUTH_SECRET", "value": "stun-only"},
                {"name": "NB_LOG_LEVEL", "value": "warn"},
            ],
            "portMappings": [{"containerPort": 3478, "hostPort": 3478, "protocol": "udp"}],
            "healthCheck": health_check(
                "/busybox/sh",
                STUN_PROBE,
                interval=5,
                retries=2,
                start_period=30,
                on_unhealthy="kill -TERM 1",
            ),
            "restartPolicy": {"enabled": True, "restartAttemptPeriod": 300},
            "stopTimeout": 15,
            "logConfiguration": logs("stun"),
        },
    ]
