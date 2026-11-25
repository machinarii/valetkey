#!/bin/bash
# Run on the Proxmox host as root, from a copy of this repo. Creates the bellhop VM with the
# broker's VM script (Debian 13 cloud image, virtual TPM, repo copied in over SSH), then runs
# bellhop/install-bellhop.sh in it. Override with env vars: VMID (default 212), BELLHOP_HOSTNAME
# (default desk), STORAGE, BRIDGE, CORES, MEMORY, DISK.
set -euo pipefail
exec env ROLE=bellhop VMID="${VMID:-212}" BROKER_HOSTNAME="${BELLHOP_HOSTNAME:-desk}" \
  CORES="${CORES:-2}" MEMORY="${MEMORY:-2048}" DISK="${DISK:-16G}" \
  bash "$(dirname "$0")/../deploy/proxmox/broker-vm.sh"
