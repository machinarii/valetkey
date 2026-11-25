#!/bin/bash
# Run on the Proxmox host as root, from a copy of this repo: copies the repo into the bellhop VM and
# runs one of its scripts there with sudo. VMID defaults to 212.
#   bellhop/proxmox-bellhop-run.sh bellhop/bellhop-tools.sh agent-dev       install and list the starter tools
#   bellhop/proxmox-bellhop-run.sh bellhop/install-bellhop.sh            update the bellhop's own code
#   bellhop/proxmox-bellhop-run.sh shell                              open a shell in the bellhop VM (for sign-ins)
set -euo pipefail
VMID=${VMID:-212}
repo=$(cd "$(dirname "$0")/.." && pwd)
script=${1:?usage: proxmox-bellhop-run.sh SCRIPT [ARGS...]}
shift
[ "$script" = shell ] || [ -f "$repo/$script" ] || { echo "no such script in this repo: $script" >&2; exit 1; }
# The VM's LAN address: the tailnet address is closed to SSH on purpose.
ip=$(qm guest cmd "$VMID" network-get-interfaces | python3 -c '
import json, sys
for i in json.load(sys.stdin):
    if i["name"] == "lo" or i["name"].startswith("tailscale"): continue
    for a in i.get("ip-addresses", []):
        if a["ip-address-type"] == "ipv4": print(a["ip-address"]); sys.exit()')
[ -n "$ip" ] || { echo "could not find the LAN address of VM $VMID" >&2; exit 1; }
[ "$script" != shell ] || exec ssh -t "admin@$ip"
tar -C "$repo" --exclude .git -czf - . \
  | ssh "admin@$ip" 'rm -rf /tmp/valetkey && mkdir -p /tmp/valetkey && tar -C /tmp/valetkey -xzf -'
ssh -t "admin@$ip" sudo bash "/tmp/valetkey/$script" "$@"
