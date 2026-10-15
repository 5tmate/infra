#!/usr/bin/env bash
set -uo pipefail
url=${PROBE_URL:-https://netbird.5tmate.threatreveal.org/oauth2/.well-known/openid-configuration}
out=${1:?usage: probe.sh <output file>}
while true; do
  code=$(curl -4 -s -o /dev/null -w '%{http_code}' --max-time 2 "$url") || code=000
  printf '%s %s\n' "$(date +%T)" "$code" >>"$out"
  sleep 1
done
