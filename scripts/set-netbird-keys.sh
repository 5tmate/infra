#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../netbird"
export AWS_PROFILE="${AWS_PROFILE:-pulumi}"
source ../scripts/get-passphrase.sh netbird

for key in auth_secret session_key store_encryption_key; do
  if pulumi config get "$key" >/dev/null 2>&1; then
    echo "$key already set, keeping it"
    continue
  fi
  value=$(python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())')
  pulumi config set --secret "$key" "$value"
  echo "$key set on stack $(pulumi stack --show-name)"
done
