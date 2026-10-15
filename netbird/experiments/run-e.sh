#!/usr/bin/env bash
set -euo pipefail
PREFIX=${1:-實驗E}
source "$(dirname "$0")/lib.sh"

run_ssm() {
  local id; id=$(aws ssm send-command --instance-ids "$primary" \
    --document-name AWS-RunShellScript --parameters "commands=[\"$1\"]" \
    --query 'Command.CommandId' --output text)
  log "SSM on $primary: $1"
  [ "${2:-}" = wait ] || return 0
  aws ssm wait command-executed --command-id "$id" --instance-id "$primary" 2>/dev/null || true
}

preflight
start_probe
arm

run_ssm "sysctl -w kernel.panic=0 kernel.sysrq=1" wait
run_ssm "echo c > /proc/sysrq-trigger"
log "kernel panic triggered on $primary"

wait_until 900 "service back on a new primary" settled
