#!/bin/bash
# Runs inside the Vaultwarden container or VM as root (called by deploy/proxmox/vaultwarden-ct.sh,
# or by hand on any Debian/Ubuntu host: copy deploy/vaultwarden/ to /opt/vaultwarden and client/{broker,install-version-tools} to /opt/vaultwarden/client first).
# Installs Docker and Tailscale, starts Vaultwarden on 127.0.0.1:8080 and publishes it
# on the tailnet only, over HTTPS, with `tailscale serve`.
set -euo pipefail

VW_VERSION=${VW_VERSION:-1.37.4}   # keep >= 1.37.2 for Bitwarden clients 2026.8+; check https://github.com/dani-garcia/vaultwarden/releases
DIR=/opt/vaultwarden
VAULT_HOSTNAME=${VAULT_HOSTNAME:-vault}

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q curl ca-certificates jq argon2 openssl python3
# The top-level installer and Proxmox stage this next to the vault scripts.
if [ -f "$DIR/client/install-version-tools" ]; then
  bash "$DIR/client/install-version-tools"
elif [ -f "$(dirname "$0")/../../client/install-version-tools" ]; then
  bash "$(dirname "$0")/../../client/install-version-tools"
else
  echo "Standalone script copy: install the version commands from a valetkey checkout with sudo ./install.sh update." >&2
fi

command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now docker tailscaled

if ! tailscale status >/dev/null 2>&1; then
  echo
  echo ">>> Open the login URL below in your browser to add this container to the tailnet."
  tailscale up --hostname="$VAULT_HOSTNAME" --advertise-tags=tag:vault
fi
FQDN=$(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')

cd "$DIR"
cat > compose.yaml <<EOF
services:
  vaultwarden:
    image: vaultwarden/server:${VW_VERSION}
    container_name: vaultwarden
    restart: unless-stopped
    env_file:
      - ./vaultwarden.env
      - path: ./secrets.env
        required: false
    volumes:
      - ./data:/data
    ports:
      - "127.0.0.1:8080:80"
EOF

if [ ! -f vaultwarden.env ]; then
  cat > vaultwarden.env <<EOF
DOMAIN=https://${FQDN}
SIGNUPS_ALLOWED=true
INVITATIONS_ALLOWED=false
PASSWORD_HINTS_ALLOWED=false
SHOW_PASSWORD_HINT=false
IP_HEADER=X-Forwarded-For
LOG_LEVEL=warn
EOF
fi
[ -f secrets.env ] || install -m 600 /dev/null secrets.env

docker compose up -d
tailscale serve --bg --https=443 http://127.0.0.1:8080

echo
echo "Vaultwarden is up at: https://${FQDN}"
echo "Sign-ups are OPEN until you run /opt/vaultwarden/close-signups.sh."
