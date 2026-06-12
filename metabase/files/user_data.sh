#!/bin/bash
set -euxo pipefail
dnf install -y docker
systemctl enable --now docker
docker run -d --name metabase --restart unless-stopped \
  -p 127.0.0.1:3000:3000 metabase/metabase:__METABASE_TAG__
