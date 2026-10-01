#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../netbird"
export AWS_PROFILE="${AWS_PROFILE:-pulumi}"
source ../scripts/get-passphrase.sh netbird

python3 -c 'import bcrypt' 2>/dev/null || { echo "python3 needs the bcrypt package: pip install bcrypt" >&2; exit 1; }

read -rsp "owner password: " password; echo
read -rsp "again: " confirm; echo
[ "$password" = "$confirm" ] || { echo "passwords do not match" >&2; exit 1; }
[ "${#password}" -ge 8 ] || { echo "use at least 8 characters" >&2; exit 1; }

hash=$(python3 -c 'import bcrypt, sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())' "$password")
pulumi config set --secret owner_password_hash "$hash"
echo "owner_password_hash set on stack $(pulumi stack --show-name)"
