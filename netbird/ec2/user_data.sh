#!/bin/bash
set -euxo pipefail

BUCKET=__BUCKET__
REGION=__REGION__
CLUSTER=__CLUSTER__
SERVICE=__SERVICE__
ROLE=__ROLE__
NB_DIR=__NB_DIR__

cat > /usr/local/bin/netbird-prepare <<PREPARE
#!/bin/bash
set -euxo pipefail

BUCKET=${BUCKET}
REGION=${REGION}
ROLE=${ROLE}
NB_DIR=${NB_DIR}
PREPARE

cat >> /usr/local/bin/netbird-prepare <<'PREPARE'

retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    [ "$n" -ge 5 ] && return 1
    sleep $((n * 3))
  done
}

install -d "${NB_DIR}/data/letsencrypt"

retry aws s3 sync "s3://${BUCKET}/config/letsencrypt/" "${NB_DIR}/data/letsencrypt/" --region "$REGION" --only-show-errors
chmod -R go-rwx "${NB_DIR}/data/letsencrypt"

retry aws s3 sync "s3://${BUCKET}/config/geolite/" "${NB_DIR}/data/" --region "$REGION" --only-show-errors \
  --exclude "*" --include "GeoLite2-City_*.mmdb" --include "geonames_*.db"

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

cat > /usr/local/bin/netbird-update-geolite <<GEO
#!/bin/bash
set -euo pipefail
BUCKET=${BUCKET}
REGION=${REGION}
NB_DIR=${NB_DIR}
GEO

cat >> /usr/local/bin/netbird-update-geolite <<'GEO'
URL="https://pkgs.netbird.io/geolocation-dbs/GeoLite2-City/download?suffix=tar.gz"
DEST="s3://${BUCKET}/config/geolite"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

aws s3 sync "${NB_DIR}/data/" "$DEST/" --region "$REGION" --only-show-errors --exclude "*" --include "geonames_*.db"

curl -fsSL --retry 3 -o "$TMP/db.tar.gz" "$URL"
curl -fsSL --retry 3 -o "$TMP/db.sha256" "${URL}.sha256"
echo "$(cut -d' ' -f1 "$TMP/db.sha256")  $TMP/db.tar.gz" | sha256sum -c --quiet
tar -xzf "$TMP/db.tar.gz" -C "$TMP"
SRC=$(find "$TMP" -name GeoLite2-City.mmdb | head -1)
NAME="$(basename "$(dirname "$SRC")").mmdb"
case "$NAME" in GeoLite2-City_*.mmdb) ;; *) echo "unexpected archive layout: $SRC" >&2; exit 1 ;; esac

[ -e "${NB_DIR}/data/${NAME}" ] || install -m 0644 "$SRC" "${NB_DIR}/data/${NAME}"
aws s3 cp "${NB_DIR}/data/${NAME}" "$DEST/${NAME}" --region "$REGION" --only-show-errors
for old in $(aws s3 ls "$DEST/" --region "$REGION" | awk '{print $4}'); do
  case "$old" in GeoLite2-City_*.mmdb) [ "$old" = "$NAME" ] || aws s3 rm "$DEST/$old" --region "$REGION" --only-show-errors ;; esac
done
GEO
chmod +x /usr/local/bin/netbird-update-geolite

cat > /etc/systemd/system/netbird-update-geolite.service <<'UNIT'
[Unit]
Description=Fetch the newest GeoLite2 City database into the data directory and S3, keeping the old one if anything fails
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/netbird-update-geolite
UNIT

cat > /etc/systemd/system/netbird-update-geolite.timer <<'UNIT'
[Unit]
Description=Monthly GeoLite2 update

[Timer]
OnCalendar=monthly
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat > /usr/local/bin/netbird-kick <<KICK
#!/bin/bash
set -euo pipefail
REGION=${REGION}
CLUSTER=${CLUSTER}
SERVICE=${SERVICE}
KICK

cat >> /usr/local/bin/netbird-kick <<'KICK'
until curl -sf http://localhost:51678/v1/metadata | grep -q '"ContainerInstanceArn":"arn:'; do sleep 2; done
read -r running pending desired < <(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --region "$REGION" --query 'services[0].[runningCount,pendingCount,desiredCount]' --output text)
if [ "$running" = 0 ] && [ "$pending" = 0 ]; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --region "$REGION" \
    --desired-count "$desired" --query service.serviceName --output text
fi
KICK
chmod +x /usr/local/bin/netbird-kick

cat > /etc/systemd/system/netbird-kick.service <<'UNIT'
[Unit]
Description=Ask ECS to place the NetBird task as soon as this instance joins, instead of waiting for its next retry
After=ecs.service
Wants=ecs.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/netbird-kick
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
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
systemctl enable --now netbird-update-geolite.timer
systemctl start netbird-prepare.service

if [ "$ROLE" = "primary" ]; then
  systemctl enable netbird-kick.service
  systemctl start --no-block netbird-kick.service
fi

if [ "$ROLE" = "standby" ]; then
  systemctl mask --runtime ecs.service
  systemctl stop ecs.service
  shutdown -h +1
fi
