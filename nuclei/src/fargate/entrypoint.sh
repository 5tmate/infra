#!/bin/sh
set -eu

echo "[nuclei-runner] run_id=${RUN_ID:-unset} target=${TARGET}"
echo "[nuclei-runner] templates=${TEMPLATES}"
exec nuclei -target "${TARGET}" -templates "${TEMPLATES}" -no-color -jsonl
