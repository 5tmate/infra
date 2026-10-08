#!/usr/bin/env bash
set -euo pipefail
NAME=5tmate-netbird
URL=https://netbird.5tmate.threatreveal.org/oauth2/.well-known/openid-configuration
DASHBOARD=https://admin-netbird.5tmate.threatreveal.org/
export AWS_REGION=ap-northeast-1
out=$(realpath -m "${RESULTS_DIR:-$(dirname "$0")/results}")/$(date +%Y%m%d)/${1:-實驗D}-$(date +%Y%m%d-%H%M%S)
cd "$(dirname "$0")"
mkdir -p "$out"

log() { echo "$(date +%T) $*" | tee -a "$out/actions.log"; }
code() { curl -4 -s -o /dev/null -w '%{http_code}' --max-time 5 "$1"; }

primary=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$NAME" \
  --query "AutoScalingGroups[0].Instances[?LifecycleState=='InService'].InstanceId" --output text)
[ -n "$primary" ] || { log "no primary in service, aborting"; exit 1; }
eip=$(aws ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" \
  --query 'Addresses[0].InstanceId' --output text)
ci=$(aws ecs list-container-instances --cluster "$NAME" --filter "ec2InstanceId == $primary" \
  --query 'containerInstanceArns[0]' --output text)
if [ "$eip" != "$primary" ] || [ "$ci" = None ] || [ "$(code "$URL")" != 200 ]; then
  log "service not healthy, aborting"
  exit 1
fi
log "primary $primary, container instance ${ci##*/}"
if [ -n "${DRY_RUN:-}" ]; then
  log "dry run, dashboard $(code "$DASHBOARD"), management $(code "$URL")"
  exit 0
fi

[ -x .venv/bin/python ] || { uv venv -q .venv && uv pip install -q --python .venv/bin/python 'boto3[crt]'; }
.venv/bin/python -u monitor.py --out "$out" &
pid=$!
for _ in $(seq 90); do grep -q baseline "$out/events.log" 2>/dev/null && break; sleep 1; done
sleep 10

aws ecs update-container-instances-state --cluster "$NAME" --container-instances "$ci" \
  --status DRAINING --output text >/dev/null
log "set primary $primary to DRAINING"

for _ in $(seq 30); do
  d=$(code "$DASHBOARD")
  m=$(code "$URL")
  log "dashboard=$d management=$m"
  [ "$d" = 200 ] || [ "$m" = 200 ] || break
  sleep 10
done

wait "$pid" || true
log "monitor finished"
