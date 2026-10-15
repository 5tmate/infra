#!/usr/bin/env bash
set -euo pipefail
PREFIX=${1:-實驗B}
source "$(dirname "$0")/lib.sh"

preflight
start_probe
arm

lower_price
terminate_primary
wait_until 1200 "service up on the standby" on_standby

restore_price
wait_until 1200 "failed back to a new primary" settled
