#!/bin/bash
# Run on the Proxmox host as root. Creates an unprivileged Debian LXC for Vaultwarden,
# copies the in-container scripts, and runs the container setup.
# Override defaults with env vars, e.g.: CTID=310 STORAGE=local-zfs bash deploy/proxmox/vaultwarden-ct.sh
# VAULT_HOSTNAME sets the container's hostname and tailnet name (default vault).
set -euo pipefail

CTID=${CTID:-210}
STORAGE=${STORAGE:-local-lvm}        # rootfs storage
TPL_STORAGE=${TPL_STORAGE:-local}    # template storage
BRIDGE=${BRIDGE:-vmbr0}
VAULT_HOSTNAME=${VAULT_HOSTNAME:-vault}
HERE=$(cd "$(dirname "$0")/.." && pwd)   # deploy/

[ "$(id -u)" = 0 ] || { echo "Run as root on the Proxmox host." >&2; exit 1; }
if pct status "$CTID" >/dev/null 2>&1 || qm status "$CTID" >/dev/null 2>&1; then
  echo "ID $CTID is already in use. Set CTID to a free ID." >&2; exit 1
fi

bash "$HERE/../client/install-version-tools"

ARCH=$(dpkg --print-architecture)   # templates exist for amd64 and arm64; match the host
pveam update >/dev/null
TPL=$(pveam available --section system | awk -v a="_${ARCH}." '/debian-13-standard/ && index($2, a) {print $2}' | sort -V | tail -1)
[ -n "$TPL" ] || TPL=$(pveam available --section system | awk -v a="_${ARCH}." '/debian-12-standard/ && index($2, a) {print $2}' | sort -V | tail -1)
[ -n "$TPL" ] || { echo "No Debian template found for $ARCH." >&2; exit 1; }
echo "Using template $TPL"
pveam list "$TPL_STORAGE" | grep -q "$TPL" || pveam download "$TPL_STORAGE" "$TPL"

pct create "$CTID" "$TPL_STORAGE:vztmpl/$TPL" \
  --hostname "$VAULT_HOSTNAME" --ostype debian --unprivileged 1 \
  --features nesting=1,keyctl=1 \
  --cores 2 --memory 1024 --swap 512 \
  --rootfs "$STORAGE:8" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --dev0 /dev/net/tun \
  --onboot 1
pct start "$CTID"

echo "Waiting for the container network..."
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done

pct exec "$CTID" -- mkdir -p /opt/vaultwarden
for f in vaultwarden-setup.sh set-admin-token.sh close-signups.sh; do
  pct push "$CTID" "$HERE/vaultwarden/$f" "/opt/vaultwarden/$f" --perms 0755
done
pct exec "$CTID" -- mkdir -p /opt/vaultwarden/client
for f in relay install-version-tools; do
  pct push "$CTID" "$HERE/../client/$f" "/opt/vaultwarden/client/$f" --perms 0755
done
pct exec "$CTID" -- env VAULT_HOSTNAME="$VAULT_HOSTNAME" bash /opt/vaultwarden/vaultwarden-setup.sh
