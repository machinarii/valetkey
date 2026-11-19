#!/bin/bash
# Run inside the Vaultwarden container as root once your account and the relay accounts exist.
set -euo pipefail
cd /opt/vaultwarden
sed -i 's/^SIGNUPS_ALLOWED=.*/SIGNUPS_ALLOWED=false/' vaultwarden.env
docker compose up -d --force-recreate >/dev/null
grep '^SIGNUPS_ALLOWED' vaultwarden.env
