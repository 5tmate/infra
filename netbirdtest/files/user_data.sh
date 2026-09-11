#!/bin/bash
set -euxo pipefail

DOMAIN=__DOMAIN__
BUCKET=__BUCKET__
REGION=__REGION__
LE_EMAIL=__LE_EMAIL__
LITESTREAM_VERSION=__LITESTREAM_VERSION__
EIP_ALLOC=__EIP_ALLOC__
EIP_ADDR=__EIP_ADDR__
STANDBY_NAME=__STANDBY_NAME__
NB_DIR=/home/ec2-user/netbird

dnf install -y docker jq sqlite
install -d /usr/local/lib/docker/cli-plugins
curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
systemctl enable --now docker
usermod -aG docker ec2-user
docker compose version

curl -fsSL -o /tmp/litestream.rpm \
  "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.rpm"
rpm -i /tmp/litestream.rpm
systemctl disable litestream || true
litestream version

cat > /usr/local/lib/netbird-common.sh <<COMMON
DOMAIN=${DOMAIN}
BUCKET=${BUCKET}
REGION=${REGION}
EIP_ALLOC=${EIP_ALLOC}
EIP_ADDR=${EIP_ADDR}
STANDBY_NAME=${STANDBY_NAME}
NB_DIR=${NB_DIR}
DBS="store idp events"
COMMON

cat >> /usr/local/lib/netbird-common.sh <<'COMMON'

imds() {
  local token
  token=$(curl -sX PUT http://169.254.169.254/latest/api/token \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  curl -s -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/$1"
}

volume_path() {
  local name
  name=$(docker volume ls -q --filter "name=$1" | head -1)
  test -n "$name"
  docker volume inspect -f '{{.Mountpoint}}' "$name"
}

data_dir() { volume_path netbird_data; }

write_litestream_config() {
  cat > /etc/litestream.yml <<YML
dbs:
  - path: ${1}/store.db
    replica:
      url: s3://${BUCKET}/store
      region: ${REGION}
  - path: ${1}/idp.db
    replica:
      url: s3://${BUCKET}/idp
      region: ${REGION}
  - path: ${1}/events.db
    replica:
      url: s3://${BUCKET}/events
      region: ${REGION}
YML
}

claim_eip() {
  aws ec2 associate-address --allocation-id "$EIP_ALLOC" \
    --instance-id "$(imds instance-id)" --allow-reassociation --region "$REGION"

  for _ in $(seq 1 30); do
    if [ "$(imds public-ipv4)" = "$EIP_ADDR" ]; then
      break
    fi
    sleep 2
  done
  test "$(imds public-ipv4)" = "$EIP_ADDR"

  for _ in $(seq 1 90); do
    if [ "$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')" = "$EIP_ADDR" ]; then
      break
    fi
    sleep 10
  done
  test "$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')" = "$EIP_ADDR"
}

standby_state() {
  aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=${STANDBY_NAME}" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].State.Name' --output text
}
COMMON

cat > /usr/local/bin/netbird-flush <<'SCRIPT'
#!/bin/bash
set -euo pipefail
. /usr/local/lib/netbird-common.sh
systemctl is-active --quiet litestream || exit 0
DATA_DIR=$(data_dir)
for db in $DBS; do
  litestream sync -wait -timeout 30 "${DATA_DIR}/${db}.db"
done
SCRIPT
chmod +x /usr/local/bin/netbird-flush

cat > /usr/local/bin/netbird-prepare <<'SCRIPT'
#!/bin/bash
set -euxo pipefail
. /usr/local/lib/netbird-common.sh
cd "$NB_DIR"

for f in docker-compose.yml config.yaml dashboard.env; do
  aws s3 cp "s3://${BUCKET}/config/${f}" "./${f}" --region "$REGION"
done
chown ec2-user:ec2-user docker-compose.yml config.yaml dashboard.env
chmod 600 config.yaml

docker compose create

ACME_DIR=$(volume_path letsencrypt)
if aws s3 ls "s3://${BUCKET}/config/acme.json" --region "$REGION" >/dev/null 2>&1; then
  aws s3 cp "s3://${BUCKET}/config/acme.json" "${ACME_DIR}/acme.json" --region "$REGION"
  chmod 600 "${ACME_DIR}/acme.json"
fi

DATA_DIR=$(data_dir)
write_litestream_config "$DATA_DIR"
for db in $DBS; do
  litestream restore -config /etc/litestream.yml -integrity-check full "${DATA_DIR}/${db}.db"
done
SCRIPT
chmod +x /usr/local/bin/netbird-prepare

cat > /usr/local/bin/netbird-takeover <<'SCRIPT'
#!/bin/bash
set -euxo pipefail
. /usr/local/lib/netbird-common.sh

claim_eip

cd "$NB_DIR"
docker compose up -d
systemctl start litestream

ACME_FILE=$(volume_path letsencrypt)/acme.json

acme_has_certificate() {
  jq -e '[.[].Certificates // [] | .[]] | length > 0' "$ACME_FILE" >/dev/null 2>&1
}

wait_for_certificate() {
  for _ in $(seq 1 24); do
    acme_has_certificate && return 0
    sleep 10
  done
  return 1
}

if ! wait_for_certificate; then
  docker compose restart traefik
  wait_for_certificate || echo "no certificate after retry; serving the Traefik default" >&2
fi

/usr/local/bin/netbird-backup-config
SCRIPT
chmod +x /usr/local/bin/netbird-takeover

cat > /usr/local/bin/netbird-standdown <<'SCRIPT'
#!/bin/bash
set -euxo pipefail
. /usr/local/lib/netbird-common.sh
cd "$NB_DIR"
docker compose stop
/usr/local/bin/netbird-flush
systemctl stop litestream || true
SCRIPT
chmod +x /usr/local/bin/netbird-standdown

cat > /etc/systemd/system/netbird-flush.service <<'UNIT'
[Unit]
Description=Flush Litestream to S3 before shutdown
After=litestream.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
ExecStop=/usr/local/bin/netbird-flush
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/netbird-takeover.service <<'UNIT'
[Unit]
Description=Claim the EIP and start serving NetBird
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/netbird-takeover
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
UNIT

cat > /usr/local/bin/netbird-backup-config <<'SCRIPT'
#!/bin/bash
set -euo pipefail
. /usr/local/lib/netbird-common.sh
cd "$NB_DIR"
for f in docker-compose.yml config.yaml dashboard.env; do
  aws s3 cp "$f" "s3://${BUCKET}/config/${f}" --region "$REGION" --only-show-errors
done
ACME=$(volume_path letsencrypt)/acme.json
if [ -s "$ACME" ]; then
  aws s3 cp "$ACME" "s3://${BUCKET}/config/acme.json" --region "$REGION" --only-show-errors
fi
SCRIPT
chmod +x /usr/local/bin/netbird-backup-config

cat > /etc/systemd/system/netbird-backup-config.service <<'UNIT'
[Unit]
Description=Back up NetBird config and ACME certificate to S3
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/netbird-backup-config
UNIT

cat > /etc/systemd/system/netbird-backup-config.timer <<'UNIT'
[Unit]
Description=Daily NetBird config backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now netbird-backup-config.timer
systemctl enable --now netbird-flush.service

install -d -o ec2-user -g ec2-user "$NB_DIR"
cd "$NB_DIR"

. /usr/local/lib/netbird-common.sh

if ! aws s3 ls "s3://${BUCKET}/config/config.yaml" --region "$REGION" >/dev/null 2>&1; then
  claim_eip
  NETBIRD_NON_INTERACTIVE=true \
  NETBIRD_DOMAIN="$DOMAIN" \
  NETBIRD_LETSENCRYPT_EMAIL="$LE_EMAIL" \
  NETBIRD_REVERSE_PROXY_TYPE=0 \
  NETBIRD_ENABLE_PROXY=false \
  NETBIRD_ENABLE_CROWDSEC=false \
    bash -c 'curl -fsSL https://github.com/netbirdio/netbird/releases/latest/download/getting-started.sh | bash'
  chown ec2-user:ec2-user docker-compose.yml config.yaml dashboard.env
  write_litestream_config "$(data_dir)"
  systemctl start litestream
  /usr/local/bin/netbird-backup-config
  exit 0
fi

/usr/local/bin/netbird-prepare

STATE=$(standby_state)
case "$STATE" in
  pending | running | stopping)
    echo "standby is ${STATE}; prepared but not serving" ;;
  *)
    /usr/local/bin/netbird-takeover ;;
esac
