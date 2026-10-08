#!/usr/bin/env bash
set -euo pipefail
NAME=5tmate-netbird
URL=https://netbird.5tmate.threatreveal.org/oauth2/.well-known/openid-configuration
TIMEOUT_MINUTES=35
export AWS_REGION=ap-northeast-1
out=$(realpath -m "${RESULTS_DIR:-$(dirname "$0")/results}")/$(date +%Y%m%d)/${1:-實驗B}-$(date +%Y%m%d-%H%M%S)
cd "$(dirname "$0")"
mkdir -p "$out"

log() { echo "$(date +%T) $*" | tee -a "$out/actions.log"; }
asg() {
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$NAME" \
    --query "AutoScalingGroups[0].$1" --output text
}
eip() {
  aws ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" \
    --query 'Addresses[0].InstanceId' --output text
}
up() { [ "$(curl -4 -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL")" = 200 ]; }
price() {
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$NAME" \
    --mixed-instances-policy "{\"InstancesDistribution\":{\"SpotMaxPrice\":\"$1\"}}"
}

primary=$(asg "Instances[?LifecycleState=='InService'].InstanceId")
standby=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME-standby" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ -z "$primary" ] || [ "$(eip)" != "$primary" ] || ! up; then
  log "service not healthy, aborting"
  exit 1
fi
log "primary $primary, standby $standby"
if [ -n "${DRY_RUN:-}" ]; then
  log "dry run, spot max price $(asg MixedInstancesPolicy.InstancesDistribution.SpotMaxPrice)"
  exit 0
fi

[ -x .venv/bin/python ] || { uv venv -q .venv && uv pip install -q --python .venv/bin/python 'boto3[crt]'; }
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$NAME" > "$out/asg-before.json"
overrides=$(asg 'length(MixedInstancesPolicy.LaunchTemplate.Overrides)')
strategy=$(asg MixedInstancesPolicy.InstancesDistribution.SpotAllocationStrategy)

.venv/bin/python -u monitor.py --out "$out" &
pid=$!
for _ in $(seq 90); do grep -q baseline "$out/events.log" 2>/dev/null && break; sleep 1; done
sleep 10

lowered=0
restore() {
  [ "$lowered" = 1 ] || return 0
  price "" && lowered=0 && log "spot max price restored"
}
trap restore EXIT
trap 'restore; exit 1' INT TERM

lowered=1
price 0.001
if [ "$(asg MixedInstancesPolicy.InstancesDistribution.SpotMaxPrice)" != 0.001 ] ||
  [ "$(asg 'length(MixedInstancesPolicy.LaunchTemplate.Overrides)')" != "$overrides" ] ||
  [ "$(asg MixedInstancesPolicy.InstancesDistribution.SpotAllocationStrategy)" != "$strategy" ]; then
  log "price change did not apply as expected, aborting"
  kill -TERM "$pid"
  wait "$pid" || true
  exit 1
fi
log "spot max price set to 0.001"

aws autoscaling terminate-instance-in-auto-scaling-group --instance-id "$primary" \
  --no-should-decrement-desired-capacity --output text >/dev/null
log "terminated primary $primary"

deadline=$(($(date +%s) + TIMEOUT_MINUTES * 60))
until [ "$(eip)" = "$standby" ] && up; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    log "no failover after $TIMEOUT_MINUTES minutes, restoring the price"
    break
  fi
  sleep 15
done
[ "$(eip)" = "$standby" ] && up && log "service is up on the standby $standby"

restore
left=$(asg MixedInstancesPolicy.InstancesDistribution.SpotMaxPrice)
[ "$left" = None ] || log "spot max price is still $left, reset it by hand"
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$NAME" > "$out/asg-after.json"

wait "$pid" || true
log "monitor finished"
