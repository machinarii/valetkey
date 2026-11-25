#!/bin/bash
# valetkey broker dependencies. Run as root on a fresh Debian 12+/Ubuntu 24.04+ machine:
#   sudo ./install.sh broker --with-deps     (or: sudo bash broker/deps.sh)
# Installs: python3 (>= 3.11), jq, curl, unzip, gpg, git and ssh (for valetkey-broker-update), Tailscale,
# the Bitwarden CLI (official native build) and fnox (pinned, through mise, system-wide under
# /opt/mise). Then brings the machine onto the tailnet as BROKER_HOSTNAME with tag:broker if it
# is not on one yet.
# Idempotent; installs are logged with versions and checksums in /var/log/valetkey-install.log.
#
#   FNOX_VERSION     fnox version to pin           (default 1.35.3)
#   BROKER_HOSTNAME  tailnet machine name          (default broker)
#   BROKER_TAGS      tags to advertise             (default tag:broker)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
command -v apt-get >/dev/null || { echo "deps.sh supports Debian/Ubuntu (apt) only." >&2; exit 1; }

FNOX_VERSION=${FNOX_VERSION:-1.35.3}
BROKER_HOSTNAME=${BROKER_HOSTNAME:-broker}
BROKER_TAGS=${BROKER_TAGS:-tag:broker}
log=/var/log/valetkey-install.log
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get install -y -q curl ca-certificates unzip jq gpg python3 qrencode git openssh-client >/dev/null
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "python3 >= 3.11 needed (Debian 12+, Ubuntu 24.04+)." >&2; exit 1; }
command -v systemd-creds >/dev/null || { echo "systemd-creds missing (systemd >= 250 needed)." >&2; exit 1; }
echo "ok    apt packages, python3 $(/usr/bin/python3 -c 'import platform; print(platform.python_version())')"

# Tailscale
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled >/dev/null
# Keep the broker reachable: restart tailscaled whatever the exit reason.
install -d /etc/systemd/system/tailscaled.service.d
printf '[Service]\nRestart=always\nRestartSec=5\n' > /etc/systemd/system/tailscaled.service.d/restart.conf
systemctl daemon-reload
if ! tailscale status >/dev/null 2>&1; then
  echo
  echo ">>> Open the login URL below in your browser to add the broker to the tailnet."
  tailscale up --hostname="$BROKER_HOSTNAME" --advertise-tags="$BROKER_TAGS"
fi
echo "ok    tailscale $(tailscale version | head -1)"

# Bitwarden CLI (official native build; x86_64 only upstream, arm64 falls back to npm)
if ! command -v bw >/dev/null; then
  case $(uname -m) in
    x86_64)
      tmp=$(mktemp -d)
      curl -fsSL -o "$tmp/bw.zip" "https://vault.bitwarden.com/download/?app=cli&platform=linux"
      unzip -q -o "$tmp/bw.zip" -d "$tmp"
      install -m 0755 "$tmp/bw" /usr/local/bin/bw
      echo "$(date -Is) bw $(/usr/local/bin/bw --version) zip-sha256 $(sha256sum "$tmp/bw.zip" | cut -d' ' -f1)" >> "$log"
      rm -rf "$tmp" ;;
    *)
      apt-get install -y -q nodejs npm >/dev/null
      npm install -g --silent @bitwarden/cli
      echo "$(date -Is) bw $(bw --version) (npm)" >> "$log" ;;
  esac
fi
echo "ok    bw $(bw --version)"

# fnox via mise, system-wide under /opt/mise, linked as /usr/local/bin/fnox
if ! /usr/local/bin/fnox --version 2>/dev/null | grep -q "$FNOX_VERSION"; then
  if ! command -v mise >/dev/null; then
    install -dm 755 /etc/apt/keyrings
    curl -fsSL https://mise.jdx.dev/gpg-key.pub | gpg --dearmor --yes -o /etc/apt/keyrings/mise-archive-keyring.gpg
    echo "deb [signed-by=/etc/apt/keyrings/mise-archive-keyring.gpg arch=$(dpkg --print-architecture)] https://mise.jdx.dev/deb stable main" \
      > /etc/apt/sources.list.d/mise.list
    apt-get update -q && apt-get install -y -q mise >/dev/null
  fi
  export MISE_DATA_DIR=/opt/mise MISE_CACHE_DIR=/var/cache/mise
  mise install "fnox@$FNOX_VERSION"
  ln -sf "$(mise exec "fnox@$FNOX_VERSION" -- sh -c 'command -v fnox')" /usr/local/bin/fnox
  echo "$(date -Is) $(/usr/local/bin/fnox --version)" >> "$log"
fi
echo "ok    $(/usr/local/bin/fnox --version)"

install -dm 700 /etc/credstore.encrypted
echo
echo "Broker on the tailnet as: $(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')"
