#!/bin/bash
set -euxo pipefail
dnf install -y docker jq
install -d /usr/local/lib/docker/cli-plugins
curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
systemctl enable --now docker
usermod -aG docker ec2-user
docker compose version
