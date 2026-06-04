#!/bin/sh
set -eu

echo "[nuclei-runner] run_id=${RUN_ID:-unset} target=${TARGET}"

if [ -n "${TAGS:-}" ]; then
  echo "[nuclei-runner] tags=${TAGS}"
  exec nuclei -target "${TARGET}" -tags "${TAGS}" -no-color -jsonl
else
  echo "[nuclei-runner] templates=${TEMPLATES}"
  exec nuclei -target "${TARGET}" -templates "${TEMPLATES}" -no-color -jsonl
fi
