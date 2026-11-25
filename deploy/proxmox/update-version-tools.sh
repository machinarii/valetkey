#!/bin/bash
# Update the Valetkey CLI from this checkout on existing Proxmox roles.
set -euo pipefail
if [[ $# != 3 || $1 != +([0-9]) || $2 != +([0-9]) || $3 != +([0-9]) ||
      $1 = "$2" || $2 = "$3" || $1 = "$3" ]]; then
  echo "Usage: update-version-tools.sh VAULT_CT_ID BROKER_VM_ID BELLHOP_VM_ID" >&2
  exit 2
fi
vault=$1 broker=$2 bellhop=$3
repo=$(cd "$(dirname "$0")/../.." && pwd)
version=$(cat "$repo/VERSION")

guest() {
  qm guest exec "$@" | python3 -I -c '
import json, sys
result = json.load(sys.stdin)
if not result.get("exited") or result.get("exitcode") != 0:
    sys.exit("Guest command failed or timed out")
'
}

# Verify the selected roles before installing anywhere.
pct exec "$vault" -- test -f /opt/vaultwarden/compose.yaml </dev/null
guest "$broker" -- /bin/sh -c 'test -x /usr/local/libexec/broker/bin/broker-check || test -x /usr/local/sbin/broker-check'
guest "$bellhop" -- /usr/bin/test -f /usr/local/libexec/broker/server.py

payload=$(tar -czf - -C "$repo/client" broker install-version-tools | base64 -w0)
apply=$(cat <<'INSTALL'
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
printf '%s' "$1" | base64 -d | tar -xzf - -C "$work"
bash "$work/install-version-tools"
test "$(/usr/local/bin/valetkey-broker version)" = "$2"
INSTALL
)
bash -c "$apply" _ "$payload" "$version"
echo "Proxmox host CLI $version"
pct exec "$vault" -- /bin/bash -c "$apply" _ "$payload" "$version" </dev/null
echo "Vault CT $vault CLI $version"
for vm in "$broker" "$bellhop"; do
  guest "$vm" -- /bin/bash -c "$apply" _ "$payload" "$version"
  echo "VM $vm CLI $version"
done
