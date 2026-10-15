#!/usr/bin/env bash
set -euo pipefail
PREFIX=${1:-實驗F}
HOLD_MINUTES=${HOLD_MINUTES:-8}
source "$(dirname "$0")/lib.sh"

preflight
start_probe
arm

lower_price
terminate_primary

deadline=$(( $(date +%s) + HOLD_MINUTES * 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  log "ASG latest: $(asg 'Activities[0].[StatusCode,Description]' | tr '\t' ' ')"
  log "standby $(standby State.Name), eip on $(eip_holder)"
  sleep 30
done

restore_price
wait_until 1200 "failed back to a new primary" settled
