#!/bin/bash
set -euxo pipefail

BUCKET=__BUCKET__
REGION=__REGION__
CLUSTER=__CLUSTER__
LITESTREAM_IMAGE=__LITESTREAM_IMAGE__
ROLE=__ROLE__
NB_DIR=__NB_DIR__

cat > /usr/local/bin/netbird-prepare <<PREPARE
#!/bin/bash
set -euxo pipefail

BUCKET=${BUCKET}
REGION=${REGION}
LITESTREAM_IMAGE=${LITESTREAM_IMAGE}
ROLE=${ROLE}
NB_DIR=${NB_DIR}
PREPARE

cat >> /usr/local/bin/netbird-prepare <<'PREPARE'

have_replica() {
  aws s3 ls "s3://${BUCKET}/$1/" --region "$REGION" >/dev/null 2>&1
}

retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    [ "$n" -ge 5 ] && return 1
    sleep $((n * 3))
  done
}

install -d "${NB_DIR}/data/letsencrypt"

retry aws s3 cp "s3://${BUCKET}/config/config.yaml" "${NB_DIR}/config.yaml" --region "$REGION"
chmod 600 "${NB_DIR}/config.yaml"

retry aws s3 sync "s3://${BUCKET}/config/letsencrypt/" "${NB_DIR}/data/letsencrypt/" --region "$REGION" --only-show-errors
chmod -R go-rwx "${NB_DIR}/data/letsencrypt"

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
  have_replica "$db" || continue
  retry docker run --rm \
    -v "${NB_DIR}/data:/var/lib/netbird" \
    -v "${NB_DIR}/litestream.yml:/etc/litestream.yml:ro" \
    "$LITESTREAM_IMAGE" restore -config /etc/litestream.yml \
    -integrity-check full -force "/var/lib/netbird/${db}.db"
done
PREPARE
chmod +x /usr/local/bin/netbird-prepare

cat > /usr/local/bin/netbird-backup-certs <<CERTS
#!/bin/bash
set -euo pipefail
BUCKET=${BUCKET}
REGION=${REGION}
NB_DIR=${NB_DIR}
CERTS

cat >> /usr/local/bin/netbird-backup-certs <<'CERTS'
CERTS_DIR="${NB_DIR}/data/letsencrypt"
[ -d "$CERTS_DIR" ] || exit 0
aws s3 sync "$CERTS_DIR/" "s3://${BUCKET}/config/letsencrypt/" --region "$REGION" --only-show-errors
CERTS
chmod +x /usr/local/bin/netbird-backup-certs

cat > /etc/systemd/system/netbird-backup-certs.service <<'UNIT'
[Unit]
Description=Copy the Let's Encrypt certificate cache to S3 so the next instance reuses it

[Service]
Type=oneshot
ExecStart=/usr/local/bin/netbird-backup-certs
UNIT

cat > /etc/systemd/system/netbird-backup-certs.timer <<'UNIT'
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
Wants=network-online.target
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
systemctl enable --now netbird-backup-certs.timer
systemctl start netbird-prepare.service

if [ "$ROLE" = "standby" ]; then
  shutdown -h +1
fi
