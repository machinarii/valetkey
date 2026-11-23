#!/bin/bash
# Run on the Proxmox host as root, from a copy of the valetkey repo. Creates the broker VM from
# the Debian 13 cloud image (with a virtual TPM for systemd-creds), copies the repo into it and
# runs `install.sh broker --with-deps` there over SSH.
# SSH access: the keys in /root/.ssh/authorized_keys plus this host's root key.
# Override defaults with env vars: VMID, STORAGE, BRIDGE, BROKER_HOSTNAME, IMG_URL, CORES, MEMORY, DISK.
# ROLE=desk builds a bellhop desk VM with the same steps (see bellhop/proxmox-desk-vm.sh).
set -euo pipefail

VMID=${VMID:-211}
STORAGE=${STORAGE:-local-lvm}
BRIDGE=${BRIDGE:-vmbr0}
ARCH=$(dpkg --print-architecture)
IMG_URL=${IMG_URL:-https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-${ARCH}.qcow2}
BROKER_HOSTNAME=${BROKER_HOSTNAME:-broker}
CORES=${CORES:-2}
MEMORY=${MEMORY:-2048}
DISK=${DISK:-8G}
ROLE=${ROLE:-broker}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
WORK=/var/lib/vz/template/cache/broker
SNIPPET=/var/lib/vz/snippets/broker-vendor.yaml

[ "$(id -u)" = 0 ] || { echo "Run as root on the Proxmox host." >&2; exit 1; }
if pct status "$VMID" >/dev/null 2>&1 || qm status "$VMID" >/dev/null 2>&1; then
  echo "ID $VMID is already in use. Set VMID to a free ID." >&2; exit 1
fi

# Cloud-init vendor data needs snippet storage on `local`; add it to the existing content list.
content=$(pvesh get /storage/local --output-format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["content"])')
bash "$REPO/client/install-version-tools"
case ",$content," in *,snippets,*) ;; *) pvesm set local --content "$content,snippets" ;; esac
mkdir -p "$(dirname "$SNIPPET")" "$WORK"
cat > "$SNIPPET" <<'EOF'
#cloud-config
packages: [qemu-guest-agent]
runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]
EOF

# Download and verify the cloud image.
img="$WORK/$(basename "$IMG_URL")"
curl -fsSL -o "$WORK/SHA512SUMS" "$(dirname "$IMG_URL")/SHA512SUMS"
curl -fL -o "$img" "$IMG_URL"
(cd "$WORK" && grep " $(basename "$img")\$" SHA512SUMS | sha512sum -c -)

[ -f /root/.ssh/id_rsa.pub ] || [ -f /root/.ssh/id_ed25519.pub ] || ssh-keygen -t ed25519 -N '' -f /root/.ssh/id_ed25519 -q
keys="$WORK/authorized_keys"
pubkeys=(/root/.ssh/id_*.pub)
if [ -f /root/.ssh/authorized_keys ]; then pubkeys+=(/root/.ssh/authorized_keys); fi
cat "${pubkeys[@]}" | grep -E '^(ssh-|ecdsa-)' | sort -u > "$keys"

qm create "$VMID" --name "$BROKER_HOSTNAME" --ostype l26 \
  --cores "$CORES" --memory "$MEMORY" --cpu host \
  --net0 "virtio,bridge=$BRIDGE" --scsihw virtio-scsi-single \
  --agent enabled=1 --onboot 1 --serial0 socket --vga serial0
qm set "$VMID" --scsi0 "$STORAGE:0,import-from=$img,discard=on"
qm resize "$VMID" scsi0 "$DISK"
qm set "$VMID" --tpmstate0 "$STORAGE:1,version=v2.0"
qm set "$VMID" --ide2 "$STORAGE:cloudinit" --boot order=scsi0 \
  --ciuser admin --sshkeys "$keys" --ipconfig0 ip=dhcp \
  --cicustom "vendor=local:snippets/$(basename "$SNIPPET")"
qm start "$VMID"

echo "Waiting for the guest agent (first boot takes a minute or two)..."
until qm agent "$VMID" ping >/dev/null 2>&1; do sleep 5; done
IP=$(qm guest cmd "$VMID" network-get-interfaces | python3 -c '
import json, sys
for i in json.load(sys.stdin):
    if i["name"] == "lo": continue
    for a in i.get("ip-addresses", []):
        if a["ip-address-type"] == "ipv4": print(a["ip-address"]); sys.exit()')
echo "$ROLE VM LAN address: $IP"

# Copy the repo (without .git) and install the broker with its dependencies.
tar -C "$REPO" --exclude .git -czf - . \
  | ssh -o StrictHostKeyChecking=accept-new "admin@$IP" 'rm -rf /tmp/valetkey && mkdir -p /tmp/valetkey && tar -C /tmp/valetkey -xzf -'
if [ "$ROLE" = desk ]; then
  ssh -t "admin@$IP" sudo env DESK_HOSTNAME="$BROKER_HOSTNAME" bash /tmp/valetkey/install.sh desk
else
  ssh -t "admin@$IP" sudo env BROKER_HOSTNAME="$BROKER_HOSTNAME" bash /tmp/valetkey/install.sh broker --with-deps
fi
