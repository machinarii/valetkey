#!/bin/bash
# valetkey installer.
#
#   sudo ./install.sh vaultwarden       (Debian/Ubuntu: Docker, Tailscale, Vaultwarden on the tailnet only)
#   sudo ./install.sh broker [--with-deps]
#   ./install.sh client --relay-url https://broker.<tailnet>.ts.net [--prefix DIR] [--agent|--owner] [--with-deps]
#   ./install.sh uninstall-client [--prefix DIR]
#   ./install.sh doctor
#   ./install.sh guidance   (offline rules for agent memory; never reads credentials)
#   ./install.sh version
#
# Run from a checkout, or straight from GitHub (it clones the repo into a temp dir first):
#   curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash -s -- client --relay-url ...
# VALETKEY_REPO / VALETKEY_REF pick another repo or branch for that clone.
#
# Never asks for, prints or stores a secret. The client side stores only the broker URL.
set -euo pipefail

usage() { sed -n '2,14p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; }
die() { echo "install.sh: $*" >&2; exit 1; }
ok() { echo "ok    $*"; }
warn() { echo "warn  $*" >&2; }

# --- find (or fetch) the repo ---
self=${BASH_SOURCE[0]:-}
repo=
if [ -n "$self" ] && [ -f "$self" ]; then
  d=$(cd "$(dirname "$self")" && pwd)
  [ -f "$d/relay/relay_read.py" ] && repo=$d
fi
if [ -z "$repo" ]; then
  command -v git >/dev/null || die "git is needed to fetch valetkey"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  git clone -q --depth 1 --branch "${VALETKEY_REF:-main}" \
    "${VALETKEY_REPO:-https://github.com/machinarii/valetkey.git}" "$tmp/valetkey" \
    || die "could not clone ${VALETKEY_REPO:-https://github.com/machinarii/valetkey.git}"
  bash "$tmp/valetkey/install.sh" "$@"
  exit $?
fi
version=$(cat "$repo/VERSION")

need_python() {  # min-minor
  local py
  py=$(command -v python3) || die "python3 not found (3.$1+ needed)"
  "$py" -c "import sys; sys.exit(sys.version_info < (3, $1))" || die "python3 >= 3.$1 needed ($py is older)"
}

config_dir() { echo "${XDG_CONFIG_HOME:-$HOME/.config}/valetkey"; }

# --- client ---
cmd_client() {
  local prefix="$HOME/.local/bin" url='' owner=no agent=no deps=no tools
  while [ $# -gt 0 ]; do
    case $1 in
      --prefix) prefix=${2:?--prefix needs a directory}; shift 2 ;;
      --relay-url) url=${2:?--relay-url needs a URL}; shift 2 ;;
      --owner) owner=yes; shift ;;
      --agent) agent=yes; shift ;;
      --with-deps) deps=yes; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "client: unknown option $1" ;;
    esac
  done
  [ "$(id -u)" != 0 ] || warn "installing the client as root; usually you want your own user"
  need_python 9
  [ "$owner" != yes ] || [ "$agent" != yes ] || die "--agent and --owner require separate machines"
  if [ -n "$url" ]; then
    printf '%s' "$url" | python3 -I -c '
import sys, urllib.parse
s = sys.stdin.read()
try:
    u = urllib.parse.urlsplit(s)
    good = (u.scheme == "https" and u.hostname and u.username is None
            and u.port in (None, 443) and u.path in ("", "/") and not u.query and not u.fragment
            and not any(c.isspace() or ord(c) < 32 for c in s))
except ValueError:
    good = False
sys.exit(0 if good else 1)
' || die "--relay-url must be an https:// origin on port 443 with no credentials, path or query"
  fi
  tools="relay vault"
  [ "$owner" = yes ] && tools="$tools vault-entry"
  [ "$agent" = no ] || tools="relay"
  install -d -m 755 "$prefix"
  for t in $tools; do
    install -m 755 "$repo/client/$t" "$prefix/$t"
  done
  if [ "$agent" = no ]; then
    install -m 755 "$repo/relay/tools/fnox-to-relay.py" "$prefix/fnox-to-relay"
  fi
  install -d -m 755 "$prefix/../share/valetkey"
  install -m 644 "$repo/agents/credential-rules.md" "$prefix/../share/valetkey/credential-rules.md"
  ok "valetkey $version client tools in $prefix: $tools"
  [ "$deps" = no ] || client_deps "$prefix" "$agent"

  if [ -n "$url" ]; then
    case $url in https://*) ;; *) die "--relay-url must start with https://" ;; esac
    install -d -m 755 "$(config_dir)"
    printf '%s\n' "${url%/}" > "$(config_dir)/relay-url"
    ok "broker URL ${url%/} saved in $(config_dir)/relay-url"
  elif [ -z "${RELAY_URL:-}" ] && [ ! -f "$(config_dir)/relay-url" ]; then
    warn "no broker URL yet: re-run with --relay-url https://broker.<tailnet>.ts.net (or set RELAY_URL)"
  fi

  case ":$PATH:" in *":$prefix:"*) ;; *) warn "$prefix is not on PATH; add it before any HashiCorp vault binary" ;; esac
  # fnox's vault provider runs the first `vault` on PATH: it must be this shim.
  local first
  first=$(command -v vault 2>/dev/null || true)
  if [ -n "$first" ] && [ "$first" != "$prefix/vault" ]; then
    warn "\`vault\` on PATH is $first, not the valetkey shim; put $prefix earlier on PATH"
  fi
  command -v tailscale >/dev/null || warn "tailscale not found: the broker only answers tailnet machines (re-run with --with-deps, or https://tailscale.com/download)"
  [ "$agent" = yes ] || command -v fnox >/dev/null || [ -x "$prefix/fnox" ] \
    || warn "fnox not found: re-run with --with-deps (or https://fnox.jdx.dev) to pull project secrets; relay and vault work without it"
  echo
  echo "Check it: relay check local <item-name>     (ok / missing / not-allowed, never values)"
  echo "Safety rules: relay guide     (offline; reference these in agent instructions)"
  if [ "$agent" = yes ]; then
    echo "Broker must list this machine in [security].agent_nodes; never trusted_value_nodes or owner_nodes."
    echo "--agent does not remove existing tools or credentials and is not an access-control boundary."
  fi
}

# Tailscale, and fnox for machines that pull project secrets (agent machines use API routes
# only and get no fnox). fnox is the version the broker is tested with, installed through mise
# for this user and linked into the prefix. Nothing here asks for or stores a secret.
client_deps() {  # prefix agent
  local prefix=$1 agent=$2 ver mise
  if command -v tailscale >/dev/null; then
    ok "tailscale present"
  elif [ "$(uname -s)" = Linux ]; then
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "Next: sudo tailscale up     (then open the login URL it prints)"
  else
    warn "install Tailscale from https://tailscale.com/download and sign in"
  fi
  [ "$agent" = no ] || return 0
  if command -v fnox >/dev/null; then ok "fnox present ($(command -v fnox))"; return 0; fi
  ver=${FNOX_VERSION:-$(sed -n 's/^FNOX_VERSION=\${FNOX_VERSION:-\(.*\)}$/\1/p' "$repo/broker/deps.sh")}
  mise=$(command -v mise || true)
  if [ -z "$mise" ]; then
    curl -fsSL https://mise.run | sh
    mise=$HOME/.local/bin/mise
  fi
  "$mise" install "fnox@$ver"
  ln -sf "$("$mise" exec "fnox@$ver" -- sh -c 'command -v fnox')" "$prefix/fnox"
  ok "fnox $ver linked as $prefix/fnox"
}

cmd_uninstall_client() {
  local prefix="$HOME/.local/bin"
  [ "${1:-}" = --prefix ] && prefix=${2:?--prefix needs a directory}
  for t in relay vault vault-entry fnox-to-relay; do
    # Only our Python scripts: never a real HashiCorp vault binary of the same name.
    if [ -f "$prefix/$t" ] && head -n 3 "$prefix/$t" | grep -q 'python3'; then rm -f "$prefix/$t"; ok "removed $prefix/$t"; fi
  done
  if [ -d "$(config_dir)" ]; then rm -rf "$(config_dir)"; ok "removed $(config_dir)"; fi
}

# --- vaultwarden: the vault server, on its own Debian/Ubuntu machine or container ---
cmd_vaultwarden() {
  [ "$(id -u)" = 0 ] || die "vaultwarden: run with sudo"
  command -v apt-get >/dev/null \
    || die "vaultwarden: Debian/Ubuntu only. Elsewhere run the vaultwarden/server image and publish it with: tailscale serve --bg --https=443 http://127.0.0.1:8080"
  install -d -m 755 /opt/vaultwarden
  install -m 755 "$repo"/deploy/vaultwarden/*.sh /opt/vaultwarden/
  bash /opt/vaultwarden/vaultwarden-setup.sh
}

# --- broker ---
cmd_broker() {
  local deps=no
  while [ $# -gt 0 ]; do
    case $1 in
      --with-deps) deps=yes; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "broker: unknown option $1" ;;
    esac
  done
  [ "$(id -u)" = 0 ] || die "broker: run with sudo"
  [ "$(uname -s)" = Linux ] || die "broker: Linux with systemd only"
  if [ "$deps" = yes ]; then bash "$repo/broker/deps.sh"; fi
  bash "$repo/broker/setup.sh"
}

# --- doctor: what is installed here, without touching any secret ---
cmd_doctor() {
  echo "valetkey $version ($repo)"
  for c in python3 tailscale fnox relay vault vault-entry bw jq docker; do
    p=$(command -v "$c" 2>/dev/null || true)
    printf '%-12s %s\n' "$c" "${p:-not found}"
  done
  local url=${RELAY_URL:-}
  [ -n "$url" ] || url=$(head -1 "$(config_dir)/relay-url" 2>/dev/null || true)
  printf '%-12s %s\n' "broker URL" "${url:-not set}"
  if command -v tailscale >/dev/null; then
    printf '%-12s %s\n' "tailnet" "$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("Self",{}).get("DNSName","?").rstrip("."))' 2>/dev/null || echo 'not connected')"
  fi
  if [ -n "$url" ] && command -v curl >/dev/null; then
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url/local/check?names=valetkey-doctor" || true)
    case $code in
      200) echo "broker       reachable; this machine is a tailnet peer it recognises" ;;
      403) echo "broker       reachable, but it does not know this machine (not a tailnet peer?)" ;;
      000|"") echo "broker       not reachable (tailnet ACL, broker down, or wrong URL)" ;;
      *) echo "broker       answered $code" ;;
    esac
  fi
  if [ -d /usr/local/libexec/relay ]; then
    printf '%-12s %s\n' "broker code" "$(cat /usr/local/libexec/relay/VERSION 2>/dev/null || echo 'installed, version unknown')"
    for u in relay-local relay-infra relay-entry relay-front relay-cert.timer; do
      printf '  %-16s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"
    done
  fi
}

case ${1:-} in
  client) shift; cmd_client "$@" ;;
  broker) shift; cmd_broker "$@" ;;
  vaultwarden) cmd_vaultwarden ;;
  uninstall-client) shift; cmd_uninstall_client "$@" ;;
  doctor) cmd_doctor ;;
  guidance) cat "$repo/agents/credential-rules.md" ;;
  version|--version) echo "valetkey $version" ;;
  ""|-h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
