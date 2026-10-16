from .common import health_check

SERVER_PROBE = """
req='GET /oauth2/.well-known/openid-configuration HTTP/1.0\\r\\nHost: __SERVER__\\r\\n\\r\\n'
line=$(printf '%b' "$req" | timeout -k 1 3 openssl s_client -quiet -connect 127.0.0.1:443 \\
  -servername __SERVER__ 2>/dev/null | head -1 | tr -d '\\r')
echo "${line:-no response}"
case "$line" in *" 200 "*) return 0 ;; esac
return 1
"""
SERVER_HOLD = 60
SKIP_HOLD_FILE = "skip-hold"
SERVER_ENTRY = """
probe() {
__PROBE__
}
skip_hold=__SKIP_HOLD__
rm -f "$skip_hold"
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
if [ -e "$skip_hold" ]; then
  rm -f "$skip_hold"
  echo "stop requested for a failback, stopping without the hold"
elif [ -z "$status" ] && probe > /dev/null; then
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


def server(*, image, server_name, region, logs):
    server_probe = SERVER_PROBE.replace("__SERVER__", server_name)
    return {
        "name": "netbird-server",
        "image": image,
        "essential": True,
        "memoryReservation": 768,
        "entryPoint": ["bash", "-c"],
        "command": [
            SERVER_ENTRY.replace("__PROBE__", server_probe.strip())
            .replace("__HOLD__", str(SERVER_HOLD))
            .replace("__SKIP_HOLD__", f"/var/lib/netbird/{SKIP_HOLD_FILE}")
        ],
        "stopTimeout": SERVER_HOLD + 7,
        "environment": [{"name": "AWS_REGION", "value": region}],
        "dependsOn": [
            {"containerName": "litestream", "condition": "START"},
            {"containerName": "config", "condition": "SUCCESS"},
            {"containerName": "dashboard", "condition": "START"},
        ],
        "portMappings": [{"containerPort": 443, "hostPort": 443, "protocol": "tcp"}],
        "healthCheck": health_check("bash", server_probe, interval=5, retries=2, start_period=300),
        "mountPoints": [
            {"sourceVolume": "netbird-data", "containerPath": "/var/lib/netbird"},
            {
                "sourceVolume": "netbird-config",
                "containerPath": "/etc/netbird",
                "readOnly": True,
            },
        ],
        "logConfiguration": logs("server"),
    }
