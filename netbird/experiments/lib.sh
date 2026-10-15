NAME=5tmate-netbird                                                              # ASG、ECS cluster、service 同名
URL=https://netbird.5tmate.threatreveal.org/oauth2/.well-known/openid-configuration
export AWS_REGION=ap-northeast-1

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
out=$(realpath -m "${RESULTS_DIR:-$here/results}")/$(date +%Y%m%d)/${PREFIX:-實驗}-$(date +%Y%m%d-%H%M%S)
probe_pid=
lowered=0
fis_role=
fis_template=
primary=

log() { echo "$(date +%T) $*" | tee -a "$out/actions.log"; }

asg() {
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$NAME" \
    --query "AutoScalingGroups[0].$1" --output text
}
eip_holder() {
  aws ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" \
    --query 'Addresses[0].InstanceId' --output text
}
standby() {
  aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME-standby" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[0].Instances[0].$1" --output text
}
primary_ci() {
  aws ecs list-container-instances --cluster "$NAME" --filter "ec2InstanceId == $1" \
    --query 'containerInstanceArns[0]' --output text
}
price() { asg MixedInstancesPolicy.InstancesDistribution.SpotMaxPrice; }
set_price() {
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$NAME" \
    --mixed-instances-policy "{\"InstancesDistribution\":{\"SpotMaxPrice\":\"$1\"}}"
}
up() { [ "$(curl -4 -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL")" = 200 ]; }

on_primary() {
  [ -n "$1" ] && [ "$1" != None ] &&
    [ -n "$(asg "Instances[?LifecycleState=='InService' && InstanceId=='$1'].InstanceId")" ]
}
on_standby() { [ "$(eip_holder)" = "$(standby InstanceId)" ] && up; }
settled() { up && on_primary "$(eip_holder)" && [ "$(standby State.Name)" = stopped ]; }

preflight() {
  mkdir -p "$out"
  primary=$(asg "Instances[?LifecycleState=='InService'].InstanceId" | tr '\t' ' ')
  local st; st=$(standby State.Name)
  if [ -z "$primary" ] || [ "$(eip_holder)" != "$primary" ] || ! up; then
    log "service not healthy (primary='$primary' eip='$(eip_holder)'), aborting"; exit 1
  fi
  if [ "$st" != stopped ]; then
    log "standby is '$st', expected stopped, aborting"; exit 1
  fi
  if [ "$(price)" != None ]; then
    log "spot max price is '$(price)', expected default, aborting"; exit 1
  fi
  log "primary $primary, standby $(standby InstanceId), price default"
  [ -z "${DRY_RUN:-}" ] || { log "dry run, nothing touched"; exit 0; }
}

lower_price() {
  lowered=1
  set_price 0.001 >/dev/null
  [ "$(price)" = 0.001 ] || { log "price change did not apply, aborting"; exit 1; }
  log "spot max price set to 0.001"
}
restore_price() {
  [ "$lowered" = 1 ] || return 0
  set_price "" >/dev/null && lowered=0 && log "spot max price restored"
  [ "$(price)" = None ] || log "spot max price is still $(price), reset it by hand"
}

terminate_primary() {
  aws autoscaling terminate-instance-in-auto-scaling-group --instance-id "$primary" \
    --no-should-decrement-desired-capacity --output text >/dev/null
  log "terminated primary $primary, desired unchanged"
}

start_probe() {
  "$here/probe.sh" "$out/probe.txt" & probe_pid=$!
  log "probe started"
}
stop_probe() {
  [ -n "$probe_pid" ] || return 0
  kill "$probe_pid" 2>/dev/null || true
  wait "$probe_pid" 2>/dev/null || true
  probe_pid=
  log "probe stopped"
}

wait_until() {
  local limit=$1 what=$2; shift 2
  local deadline=$(( $(date +%s) + limit ))
  until "$@"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "gave up after ${limit}s waiting for: $what"; return 1
    fi
    sleep 5
  done
  log "reached: $what"
}

report() {
  stop_probe
  [ -x "$here/.venv/bin/python" ] ||
    { uv venv -q "$here/.venv" && uv pip install -q --python "$here/.venv/bin/python" boto3; }
  log "CloudTrail lags a few minutes, if the report is thin re-run: .venv/bin/python report.py $out"
  "$here/.venv/bin/python" "$here/report.py" "$out" || log "report.py failed, data is in $out"
}

fis_teardown() {
  if [ -n "$fis_template" ]; then
    aws fis delete-experiment-template --id "$fis_template" >/dev/null 2>&1 &&
      log "deleted FIS template $fis_template"
    fis_template=
  fi
  if [ -n "$fis_role" ]; then
    aws iam delete-role-policy --role-name "$fis_role" --policy-name inject-asg-ice >/dev/null 2>&1 || true
    aws iam delete-role --role-name "$fis_role" >/dev/null 2>&1 && log "deleted FIS role $fis_role"
    fis_role=
  fi
  return 0
}

cleaned=0
cleanup() {
  [ "$cleaned" = 0 ] || return 0
  cleaned=1
  restore_price
  fis_teardown
  report
}
arm() { trap cleanup EXIT; trap 'exit 1' INT TERM; }
