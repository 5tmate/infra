#!/bin/bash
set -euxo pipefail

BUCKET=__BUCKET__
DOMAIN=__DOMAIN__
LE_EMAIL=__LE_EMAIL__
REGION=__REGION__
CLUSTER=__CLUSTER__
EIP_ALLOC=__EIP_ALLOC__
LITESTREAM_IMAGE=__LITESTREAM_IMAGE__
ROLE=__ROLE__
STANDBY_NAME=__STANDBY_NAME__
NB_DIR=__NB_DIR__

cat > /usr/local/bin/netbird-prepare <<PREPARE
#!/bin/bash
set -euxo pipefail

BUCKET=${BUCKET}
DOMAIN=${DOMAIN}
LE_EMAIL=${LE_EMAIL}
REGION=${REGION}
EIP_ALLOC=${EIP_ALLOC}
LITESTREAM_IMAGE=${LITESTREAM_IMAGE}
ROLE=${ROLE}
STANDBY_NAME=${STANDBY_NAME}
NB_DIR=${NB_DIR}
PREPARE

cat >> /usr/local/bin/netbird-prepare <<'PREPARE'

imds() {
  local token
  token=$(curl -sX PUT http://169.254.169.254/latest/api/token \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  curl -s -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/$1"
}

standby_state() {
  aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=${STANDBY_NAME}" \
              "Name=instance-state-name,Values=pending,running,stopping" \
    --query 'Reservations[].Instances[].State.Name' --output text
}

bootstrap() {
  install -d /usr/local/lib/docker/cli-plugins
  curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
    https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

  local work
  work=$(mktemp -d)
  cd "$work"
  NETBIRD_NON_INTERACTIVE=true \
  NETBIRD_DOMAIN="$DOMAIN" \
  NETBIRD_LETSENCRYPT_EMAIL="$LE_EMAIL" \
  NETBIRD_REVERSE_PROXY_TYPE=0 \
  NETBIRD_ENABLE_PROXY=false \
  NETBIRD_ENABLE_CROWDSEC=false \
    bash -c 'curl -fsSL https://github.com/netbirdio/netbird/releases/latest/download/getting-started.sh | bash'
  docker compose down --remove-orphans || true
  for f in config.yaml dashboard.env; do
    aws s3 cp "$f" "s3://${BUCKET}/config/${f}" --region "$REGION"
  done
  cd /
  rm -rf "$work"
}

install -d "${NB_DIR}/data" "${NB_DIR}/letsencrypt"

if ! aws s3 ls "s3://${BUCKET}/config/config.yaml" --region "$REGION" >/dev/null 2>&1; then
  bootstrap
fi

aws s3 cp "s3://${BUCKET}/config/config.yaml" "${NB_DIR}/config.yaml" --region "$REGION"
chmod 600 "${NB_DIR}/config.yaml"

if aws s3 ls "s3://${BUCKET}/config/acme.json" --region "$REGION" >/dev/null 2>&1; then
  aws s3 cp "s3://${BUCKET}/config/acme.json" "${NB_DIR}/letsencrypt/acme.json" --region "$REGION"
  chmod 600 "${NB_DIR}/letsencrypt/acme.json"
fi

cat > "${NB_DIR}/litestream.yml" <<YML
dbs:
  - path: /var/lib/netbird/store.db
    replica:
      url: s3://${BUCKET}/store
      region: ${REGION}
  - path: /var/lib/netbird/idp.db
    replica:
      url: s3://${BUCKET}/idp
      region: ${REGION}
  - path: /var/lib/netbird/events.db
    replica:
      url: s3://${BUCKET}/events
      region: ${REGION}
YML

for db in store idp events; do
  docker run --rm \
    -v "${NB_DIR}/data:/var/lib/netbird" \
    -v "${NB_DIR}/litestream.yml:/etc/litestream.yml:ro" \
    "$LITESTREAM_IMAGE" restore -config /etc/litestream.yml \
    -integrity-check full -force "/var/lib/netbird/${db}.db"
done

if [ "$ROLE" = "primary" ] && [ -z "$(standby_state)" ]; then
  aws ec2 associate-address --allocation-id "$EIP_ALLOC" \
    --instance-id "$(imds instance-id)" --allow-reassociation --region "$REGION"
fi
PREPARE
chmod +x /usr/local/bin/netbird-prepare

cat > /usr/local/bin/netbird-backup-acme <<ACME
#!/bin/bash
set -euo pipefail
BUCKET=${BUCKET}
REGION=${REGION}
NB_DIR=${NB_DIR}
ACME

cat >> /usr/local/bin/netbird-backup-acme <<'ACME'
CERT="${NB_DIR}/letsencrypt/acme.json"
[ -s "$CERT" ] || exit 0
aws s3 cp "$CERT" "s3://${BUCKET}/config/acme.json" --region "$REGION" --only-show-errors
ACME
chmod +x /usr/local/bin/netbird-backup-acme

cat > /etc/systemd/system/netbird-backup-acme.service <<'UNIT'
[Unit]
Description=Copy the Let's Encrypt certificate to S3 so the next instance reuses it

[Service]
Type=oneshot
ExecStart=/usr/local/bin/netbird-backup-acme
UNIT

cat > /etc/systemd/system/netbird-backup-acme.timer <<'UNIT'
[Unit]
Description=Hourly certificate backup

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/netbird-prepare.service <<'UNIT'
[Unit]
Description=Restore NetBird state from S3 before the ECS agent joins the cluster
After=docker.service network-online.target
Requires=docker.service
Before=ecs.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/netbird-prepare
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
UNIT

install -d /etc/systemd/system/ecs.service.d
cat > /etc/systemd/system/ecs.service.d/after-prepare.conf <<'UNIT'
[Unit]
After=netbird-prepare.service
Requires=netbird-prepare.service
UNIT

grep -q "^ECS_CLUSTER=" /etc/ecs/ecs.config 2>/dev/null || echo "ECS_CLUSTER=${CLUSTER}" >> /etc/ecs/ecs.config

systemctl daemon-reload
systemctl enable netbird-prepare.service
systemctl enable --now netbird-backup-acme.timer
systemctl start netbird-prepare.service

if [ "$ROLE" = "standby" ]; then
  shutdown -h +1
fi
