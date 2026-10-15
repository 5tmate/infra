#!/usr/bin/env bash
set -euo pipefail
PREFIX=${1:-實驗D}
source "$(dirname "$0")/lib.sh"

preflight
ci=$(primary_ci "$primary")
[ "$ci" != None ] || { log "no container instance for $primary, aborting"; exit 1; }

start_probe
arm

aws ecs update-container-instances-state --cluster "$NAME" --container-instances "$ci" \
  --status DRAINING --output text >/dev/null
log "set primary $primary to DRAINING"

wait_until 600 "service back on a new primary" settled
