#!/bin/bash
set -euxo pipefail

DOMAIN=__DOMAIN__
BUCKET=__BUCKET__
REGION=__REGION__
LE_EMAIL=__LE_EMAIL__
LITESTREAM_VERSION=__LITESTREAM_VERSION__
NB_DIR=/home/ec2-user/netbird

volume_path() {
  docker volume inspect -f '{{.Mountpoint}}' "$(docker volume ls -q --filter "name=$1")"
}

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

TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 900")
SELF=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4)

for _ in $(seq 1 90); do
  if [ "$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')" = "$SELF" ]; then
    break
  fi
  sleep 10
done
test "$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')" = "$SELF"

install -d -o ec2-user -g ec2-user "$NB_DIR"
cd "$NB_DIR"

if aws s3 ls "s3://${BUCKET}/config/config.yaml" --region "$REGION" >/dev/null 2>&1; then
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

  DATA_DIR=$(volume_path netbird_data)
  write_litestream_config "$DATA_DIR"
  for db in store idp events; do
    litestream restore -config /etc/litestream.yml -integrity-check full "${DATA_DIR}/${db}.db"
  done

  docker compose up -d
else
  NETBIRD_NON_INTERACTIVE=true \
  NETBIRD_DOMAIN="$DOMAIN" \
  NETBIRD_LETSENCRYPT_EMAIL="$LE_EMAIL" \
  NETBIRD_REVERSE_PROXY_TYPE=0 \
  NETBIRD_ENABLE_PROXY=false \
  NETBIRD_ENABLE_CROWDSEC=false \
    bash -c 'curl -fsSL https://github.com/netbirdio/netbird/releases/latest/download/getting-started.sh | bash'
  chown ec2-user:ec2-user docker-compose.yml config.yaml dashboard.env

  DATA_DIR=$(volume_path netbird_data)
  write_litestream_config "$DATA_DIR"
fi

systemctl enable --now litestream

cat > /usr/local/bin/netbird-backup-config <<BACKUP
#!/bin/bash
set -euo pipefail
cd ${NB_DIR}
for f in docker-compose.yml config.yaml dashboard.env; do
  aws s3 cp "\$f" "s3://${BUCKET}/config/\$f" --region ${REGION} --only-show-errors
done
ACME=\$(docker volume inspect -f '{{.Mountpoint}}' "\$(docker volume ls -q --filter name=letsencrypt)")/acme.json
if [ -s "\$ACME" ]; then
  aws s3 cp "\$ACME" "s3://${BUCKET}/config/acme.json" --region ${REGION} --only-show-errors
fi
BACKUP
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

ACME_FILE=$(volume_path letsencrypt)/acme.json
for _ in $(seq 1 30); do
  [ -s "$ACME_FILE" ] && break
  sleep 10
done
/usr/local/bin/netbird-backup-config
