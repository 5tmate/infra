#!/usr/bin/env bash
# Fetches the Pulumi backend URL and stack passphrase from SSM, logs in to
# the backend, and sets PULUMI_CONFIG_PASSPHRASE.
#
# Usage:
#   source scripts/get-passphrase.sh <stack>   # local — exports into current shell
#   bash scripts/get-passphrase.sh <stack>     # CI — writes to $GITHUB_ENV

_get_passphrase() {
  local stack="${1:?Error: stack name required. Usage: source get-passphrase.sh <stack>}"
  local backend_path="/5tmate/pulumi/backend"
  local passphrase_path="/5tmate/pulumi/passphrase/${stack}"
  local backend passphrase

  backend=$(aws ssm get-parameter \
    --name "$backend_path" \
    --region ap-northeast-1 \
    --query Parameter.Value \
    --output text) || {
    echo "Error: failed to fetch '${backend_path}' from SSM (see above)." >&2
    return 1
  }

  passphrase=$(aws ssm get-parameter \
    --name "$passphrase_path" \
    --region ap-northeast-1 \
    --with-decryption \
    --query Parameter.Value \
    --output text) || {
    echo "Error: failed to fetch '${passphrase_path}' from SSM (see above)." >&2
    return 1
  }

  if [ -z "$passphrase" ]; then
    echo "Error: SSM parameter '${passphrase_path}' exists but is empty." >&2
    return 1
  fi

  local backend_url="${backend}/${stack}?region=ap-northeast-1"

  pulumi login "$backend_url" || {
    echo "Error: pulumi login failed." >&2
    return 1
  }

  if [ -n "${GITHUB_ENV:-}" ]; then
    echo "::add-mask::$passphrase"
    echo "PULUMI_CONFIG_PASSPHRASE=$passphrase" >> "$GITHUB_ENV"
    echo "PULUMI_BACKEND_URL=$backend_url" >> "$GITHUB_ENV"
  else
    export PULUMI_CONFIG_PASSPHRASE="$passphrase"
    export PULUMI_BACKEND_URL="$backend_url"
  fi

  echo "PULUMI_CONFIG_PASSPHRASE set from ${passphrase_path}."
}

_get_passphrase "$@"
unset -f _get_passphrase
