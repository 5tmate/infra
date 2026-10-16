#!/bin/bash
set -euxo pipefail

REGION=__REGION__
CLUSTER=__CLUSTER__
EIP_ALLOC=__EIP_ALLOC__

retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    [ "$n" -ge 5 ] && return 1
    sleep $((n * 3))
  done
}

TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
IID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id)

retry aws ec2 associate-address --allocation-id "$EIP_ALLOC" --instance-id "$IID" \
  --allow-reassociation --region "$REGION"

grep -q "^ECS_CLUSTER=" /etc/ecs/ecs.config 2>/dev/null || echo "ECS_CLUSTER=${CLUSTER}" >> /etc/ecs/ecs.config
grep -q "^ECS_IMAGE_PULL_BEHAVIOR=" /etc/ecs/ecs.config || echo "ECS_IMAGE_PULL_BEHAVIOR=prefer-cached" >> /etc/ecs/ecs.config
systemctl restart ecs || true

cat > /usr/local/bin/relays-renew <<RENEW
#!/bin/bash
set -euo pipefail
REGION=${REGION}
CLUSTER=${CLUSTER}
RENEW
cat >> /usr/local/bin/relays-renew <<'RENEW'
aws ecs update-service --cluster "$CLUSTER" --service "$CLUSTER" --region "$REGION" \
  --force-new-deployment --query service.serviceName --output text
RENEW
chmod +x /usr/local/bin/relays-renew

cat > /etc/systemd/system/relays-renew.service <<'UNIT'
[Unit]
Description=Redeploy relays monthly so signal and relay pick up the renewed certificate
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/relays-renew
UNIT

cat > /etc/systemd/system/relays-renew.timer <<'UNIT'
[Unit]
Description=Monthly relays certificate refresh

[Timer]
OnCalendar=monthly
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now relays-renew.timer
