def make_logs(log_group, region):
    def logs(stream):
        return {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": region,
                "awslogs-stream-prefix": stream,
            },
        }

    return logs


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
