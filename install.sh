#!/bin/bash
# valetkey installer.
#
#   sudo ./install.sh vaultwarden       (Debian/Ubuntu: Docker, Tailscale, Vaultwarden on the tailnet only)
#   sudo ./install.sh broker [--with-deps]
#   sudo ./install.sh desk              (bellhop desk: runs login-holding tools for agent machines; no agent here)
#   sudo ./install.sh agent [--relay-url URL] [--desk-url URL] [--tools a,b] [--user NAME] [--no-policy]
#                                       (an agent machine in one command: clients, sweep, MCP policy; no keys)
#   ./install.sh claude [--yes] [--dry-run]
#                                       (agent machine: asks what to block, then updates CLAUDE.md and settings.json)
#   ./install.sh client --relay-url https://broker.<tailnet>.ts.net [--prefix DIR] [--agent|--owner] [--with-deps]
#   ./install.sh uninstall-client [--prefix DIR]
#   ./install.sh doctor
#   ./install.sh guidance   (offline rules for agent memory; never reads credentials)
#   ./install.sh version
#   sudo ./install.sh update [--prefix BIN_DIR]  (updates the version commands only)
#   sudo ./install.sh proxmox [--no-desk]
#                                       (Proxmox host: creates the vault, broker and desk guests)
#   ./install.sh                        (on a terminal: asks which machine this is, then runs the above)
#
# Run from a checkout, or straight from GitHub (it fetches the repo into a temp dir first):
#   curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash
# VALETKEY_REPO / VALETKEY_REF pick another repo or branch for that clone.
#
# Never asks for, prints or stores a secret. The client side stores only the broker URL.
set -euo pipefail

usage() { sed -n '2,22p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; }
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
  src=${VALETKEY_REPO:-https://github.com/machinarii/valetkey.git}
  tmp=$(mktemp -d)
  chmod 755 "$tmp"  # sudo steps and the agent's user read the copy too
  trap 'rm -rf "$tmp"' EXIT
  if command -v git >/dev/null; then
    git clone -q --depth 1 --branch "${VALETKEY_REF:-main}" "$src" "$tmp/valetkey" \
      || die "could not clone $src"
  elif [ "$src" = https://github.com/machinarii/valetkey.git ] && command -v curl >/dev/null; then
    # A fresh Debian image often has curl but no git.
    mkdir "$tmp/valetkey"
    curl -fsSL "https://github.com/machinarii/valetkey/archive/${VALETKEY_REF:-main}.tar.gz" \
      | tar -xz --strip-components=1 -C "$tmp/valetkey" || die "could not download valetkey"
  else
    die "git is needed to fetch $src"
  fi
  # `curl | bash` leaves the script on stdin; give the questions the terminal instead.
  if { : </dev/tty; } 2>/dev/null; then
    bash "$tmp/valetkey/install.sh" "$@" </dev/tty
  else
    bash "$tmp/valetkey/install.sh" "$@"
  fi
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
  [ "$(id -u)" != 0 ] || [ -n "${VALETKEY_AGENT_SETUP:-}" ] || warn "installing the client as root; usually you want your own user"
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
    echo "Claude subscription: relay guide claude shows setup-token and web login options."
    echo "Valetkey works with or without claude-mem. For memory capture setup, run relay guide claude-mem."
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
  install -d -m 755 /opt/vaultwarden/client
  install -m 755 "$repo/client/relay" "$repo/client/install-version-tools" /opt/vaultwarden/client/
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

# --- agent: everything an agent machine needs, as root-owned files the agent cannot change ---
cmd_agent() {
  local relay_url='' desk_url='' tools='' user=${SUDO_USER:-} policy=yes home t existing
  while [ $# -gt 0 ]; do
    case $1 in
      --relay-url) relay_url=${2:?--relay-url needs a URL}; shift 2 ;;
      --desk-url) desk_url=${2:?--desk-url needs a URL}; shift 2 ;;
      --tools) tools=${2:?--tools needs a comma-separated list}; shift 2 ;;
      --user) user=${2:?--user needs a name}; shift 2 ;;
      --no-policy) policy=no; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "agent: unknown option $1" ;;
    esac
  done
  [ "$(id -u)" = 0 ] || die "agent: run with sudo (it writes root-owned files the agent's user cannot change)"
  [ "$(uname -s)" = Linux ] || die "agent: Linux only. On macOS use: ./install.sh client --agent --relay-url ..."
  [ -n "$relay_url$desk_url" ] || die "agent: give --relay-url, --desk-url or both"
  [ -n "$user" ] && [ "$user" != root ] && id "$user" >/dev/null 2>&1 \
    || die "agent: say which user the agent runs as: --user NAME"
  home=$(getent passwd "$user" | cut -d: -f6)
  need_python 9
  if command -v bw >/dev/null; then
    warn "the Bitwarden CLI (bw) is installed here; an agent machine should not have a vault client"
  fi

  if [ -n "$relay_url" ]; then  # the broker client, agent mode, with the URL saved for that user
    HOME=$home XDG_CONFIG_HOME='' VALETKEY_AGENT_SETUP=1 cmd_client --agent --prefix /usr/local/bin --relay-url "$relay_url" >/dev/null
    chown -R "$user": "$home/.config/valetkey"
    ok "relay (agent mode) in /usr/local/bin, broker $relay_url"
  else
    bash "$repo/client/install-version-tools"
  fi

  if [ -n "$desk_url" ]; then  # the stand-in for tools that live on the desk
    case $desk_url in https://*) ;; http://*) warn "desk URL is plain http: test networks only" ;; *) die "--desk-url must be a URL" ;; esac
    install -m 755 "$repo/bellhop/bellhop" /usr/local/bin/bellhop
    install -d -m 755 /etc/bellhop
    printf '%s\n' "${desk_url%/}" > /etc/bellhop/url
    for t in ${tools//,/ }; do
      [[ $t =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "agent: bad tool name: $t"
      existing=$(command -v "$t" 2>/dev/null || true)
      if [ -n "$existing" ] && [ "$(readlink -f "$existing")" != /usr/local/bin/bellhop ]; then
        warn "$t is already installed at $existing and was left alone; remove it and its login to use the desk's"
        continue
      fi
      ln -sf bellhop "/usr/local/bin/$t"
    done
    ok "bellhop in /usr/local/bin, desk ${desk_url%/}${tools:+, stand-ins: $tools}"
  fi

  # Where MCP servers may live, for the sweep and for the policy below.
  install -d -m 755 /etc/bellhop
  : > /etc/bellhop/mcp-prefixes
  [ -z "$relay_url" ] || printf '%s\n' "${relay_url%/}" >> /etc/bellhop/mcp-prefixes

  install -m 755 "$repo/bellhop/sweep.py" /usr/local/sbin/bellhop-sweep
  if command -v systemctl >/dev/null; then
    cat > /etc/systemd/system/bellhop-sweep.service <<'UNIT'
# Installed by valetkey (install.sh agent). Reports logins and new tools on this machine, by name.
[Unit]
Description=bellhop sweep: logins and new tools on this agent machine
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/bellhop-sweep
SuccessExitStatus=1
UNIT
    cat > /etc/systemd/system/bellhop-sweep.timer <<'UNIT'
# Installed by valetkey (install.sh agent).
[Unit]
Description=Run the bellhop sweep every hour
[Timer]
OnBootSec=2min
OnCalendar=hourly
[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable -q --now bellhop-sweep.timer
    ok "sweep every hour (journalctl -u bellhop-sweep); findings now: $(/usr/local/sbin/bellhop-sweep --accept | tail -1)"
  else
    warn "no systemd: run /usr/local/sbin/bellhop-sweep from your own scheduler, as root"
  fi

  if [ "$policy" = yes ] && [ ! -e /etc/claude-code/managed-settings.json ] && { : </dev/tty; } 2>/dev/null; then
    cat >&2 <<'ASK'

Claude Code policy: /etc/claude-code/managed-settings.json, owned by root.
  Intent: give every Claude Code session on this machine valetkey's rules, and let it use MCP
  servers only at the broker and desk, in a file the agent's user cannot change.
  It also turns off every other MCP server here, including local and account-synced ones.
  Recommended: Y, unless this machine needs other MCP servers (T3 Code's own tools, for example).
ASK
    read -r -p "  Write it? [Y/n] " answer </dev/tty || answer=n
    case $answer in [nN]*) policy=no; warn "Claude Code policy not written (run again, or ./install.sh claude for user-level rules)" ;; esac
  fi
  if [ "$policy" = yes ]; then  # Claude Code: MCP servers only at the broker and the desk; rules as standing instructions
    if [ -e /etc/claude-code/managed-settings.json ]; then
      warn "/etc/claude-code/managed-settings.json exists and was left alone; allow MCP servers only at the broker and desk there"
    else
      install -d -m 755 /etc/claude-code
      python3 -I - "$repo" "${relay_url%/}" "${desk_url%/}" <<'PY'
import json, sys
repo, relay, desk = sys.argv[1:4]
allowed = [{"serverUrl": relay + "/*"}] if relay else []
rules = open(repo + "/agents/credential-rules.md").read()
if desk:
    allowed.append({"serverUrl": desk + "/mcp/*"})
    rules += "\n" + open(repo + "/bellhop/agent-rules.md").read()
json.dump({"allowManagedMcpServersOnly": True, "allowedMcpServers": allowed, "claudeMd": rules},
          open("/etc/claude-code/managed-settings.json", "w"), indent=2)
PY
      ok "Claude Code policy: MCP servers only at the broker and desk (this also blocks local and account-synced ones; --no-policy to skip)"
    fi
  fi

  echo
  echo "Left for you, because they are yours to decide:"
  if id -nG "$user" | tr ' ' '\n' | grep -qxE 'sudo|wheel|admin' || [ -n "$(ls /etc/sudoers.d 2>/dev/null)" ]; then
    echo "  - $user may be able to use sudo. An agent that can become root can undo everything above."
  fi
  echo "  - Join the tailnet as its own tagged machine, with grants to the broker and desk on 443 only."
  echo "  - Keep logins off this machine: no signed-in cloud tools, no .env files with real keys, no SSH keys."
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

# --- proxmox: the three guests, each with its own installer inside ---
is_proxmox_host() {
  [ "$(uname -s)" = Linux ] && command -v pveversion >/dev/null &&
    command -v pvesh >/dev/null && command -v qm >/dev/null && command -v pct >/dev/null
}

cmd_proxmox() {
  local vault=${CTID:-210} broker=${BROKER_VMID:-${VMID:-211}} desk=${DESK_VMID:-212}
  local ids=("$vault" "$broker")
  case "$#:${1:-}" in
    0:) ids+=("$desk") ;;
    1:--no-desk) ;;
    *) die "usage: install.sh proxmox [--no-desk]" ;;
  esac
  is_proxmox_host || die "proxmox: run on a Proxmox VE host with qm, pct and pvesh"
  [ "$(id -u)" = 0 ] || die "proxmox: run as root on the Proxmox host"
  # Check the entire cluster before the first guest is created.
  pvesh get /cluster/resources --type vm --output-format json | python3 -I -c '
import json, re, sys
ids = sys.argv[1:]
if any(not re.fullmatch("[1-9][0-9]{2,8}", i) for i in ids) or len(ids) != len(set(ids)):
    sys.exit("Choose distinct numeric guest IDs from 100 to 999999999.")
used = {str(row["vmid"]) for row in json.load(sys.stdin)}
overlap = used.intersection(ids)
if overlap:
    sys.exit("Guest IDs already in use: " + ", ".join(sorted(overlap)) + ". No guests created.")
' "${ids[@]}"
  CTID="$vault" bash "$repo/deploy/proxmox/vaultwarden-ct.sh"
  ROLE=broker VMID="$broker" bash "$repo/deploy/proxmox/broker-vm.sh"
  if [ "${#ids[@]}" -eq 3 ]; then
    VMID="$desk" bash "$repo/bellhop/proxmox-desk-vm.sh"
  fi
}

# --- menu: one command for every machine; asks which one this is ---
tty=${VALETKEY_TTY:-/dev/tty}  # where the answers come from (a file in tests)
ask() {  # ask VAR "question" [proposed] [optional]; answers come from fd 3 (cmd_menu opens it)
  local a hint=''
  if [ -n "${3:-}" ]; then hint=" [$3] (Enter keeps it${4:+, - for none})"
  elif [ -n "${4:-}" ]; then hint=" (Enter for none)"; fi
  while :; do
    printf '%s%s: ' "$2" "$hint" >&2
    IFS= read -r -u 3 a || die "no answer"
    if [ -n "${4:-}" ] && [ "$a" = - ]; then a=''; break; fi
    a=${a:-${3:-}}
    [ -n "$a" ] || [ -n "${4:-}" ] && break
    echo "  This one is needed; type a value." >&2
  done
  printf -v "$1" '%s' "$a"
}

ask_url() {  # ask_url VAR "question" proposed [optional]: an https:// address, asked again until it is one
  local v
  while :; do
    ask v "$2" "$3" "${4:-}"
    [ -z "$v" ] || [[ $v =~ ^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$ ]] && break
    echo "  That is not an https:// address, e.g. https://broker.<tailnet>.ts.net" >&2
  done
  printf -v "$1" '%s' "$v"
}

tailnet_suffix() {  # e.g. example.ts.net, or nothing
  local ts
  ts=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
  [ -x "$ts" ] || return 0
  "$ts" status --json 2>/dev/null | python3 -I -c '
import json, sys
try: print(json.load(sys.stdin).get("MagicDNSSuffix") or "")
except ValueError: pass' 2>/dev/null || true
}

as_root() {
  if [ "$(id -u)" = 0 ]; then bash "$repo/install.sh" "$@"
  else echo "This step needs root; sudo may ask for your password." >&2; sudo bash "$repo/install.sh" "$@"; fi
}

cmd_menu() {
  local n role relay='' desk='' tools='' suffix default='' i args=() roles=() labels=()
  exec 3<"$tty"
  suffix=$(tailnet_suffix)
  if [ "$(uname -s)" = Darwin ]; then  # a Mac is only ever a client
    roles=(agent owner consumer)
  else
    roles=(proxmox vault broker desk agent owner consumer)
  fi
  labels=(
    "proxmox|Proxmox host: creates the vault, broker and desk as separate guests"
    "vault|Vault host (Vaultwarden)"
    "broker|Broker (the relay services)"
    "desk|Desk (optional: runs tools that keep their own login)"
    "agent|Agent machine (where Claude Code, Codex or other agents run)"
    "owner|Approver device (where you enter keys and approve routes; never runs agents)"
    "consumer|Trusted server (runs an application that reads the key itself; never runs agents)")
  echo 'Which machine is this? Each part needs its own machine; see "Containment" in the README.' >&2
  echo 'At each question, Enter keeps the value shown in [brackets].' >&2
  for i in "${!roles[@]}"; do
    [ "${roles[$i]}" != proxmox ] || ! is_proxmox_host || default=$((i + 1))
    for l in "${labels[@]}"; do
      [ "${l%%|*}" != "${roles[$i]}" ] || printf '  %d) %s%s\n' $((i + 1)) "${l#*|}" \
        "$([ "$default" != $((i + 1)) ] || echo '  (recommended: this is a Proxmox host)')" >&2
    done
  done
  while :; do
    ask n "Number" "$default"
    [[ $n =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le ${#roles[@]} ] && break
    echo "  Pick a number from 1 to ${#roles[@]}." >&2
  done
  role=${roles[$((n - 1))]}
  case $role in
    agent|owner|consumer) ask_url relay "Broker URL" "${suffix:+https://broker.$suffix}" ;;
  esac
  case $role in
    proxmox) as_root proxmox ;;
    vault) as_root vaultwarden ;;
    broker) as_root broker --with-deps ;;
    desk) as_root desk ;;
    agent)
      if [ "$(uname -s)" = Darwin ]; then
        cmd_client --agent --relay-url "$relay"
      else
        ask_url desk "Desk URL (if you have a desk)" "${suffix:+https://desk.$suffix/desk}" optional
        [ -z "$desk" ] || ask tools "Desk tools to link here, comma-separated" "" optional
        args=(--user "${SUDO_USER:-$(id -un)}" --relay-url "$relay")
        [ -z "$desk" ] || args+=(--desk-url "$desk")
        [ -z "$tools" ] || args+=(--tools "$tools")
        as_root agent "${args[@]}"
      fi
      if [ "$(id -u)" != 0 ] && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
        echo >&2; echo "Next: the agent rules and settings for $(id -un)." >&2
        python3 -I "$repo/agents/claude-setup.py" --repo "$repo" <&3
      else
        echo "Then, as the agent's user: ./install.sh claude" >&2
      fi ;;
    owner) cmd_client --owner --relay-url "$relay" ;;
    consumer) cmd_client --relay-url "$relay" --with-deps ;;
  esac
}

case ${1:-} in
  client) shift; cmd_client "$@" ;;
  broker) shift; cmd_broker "$@" ;;
  vaultwarden) cmd_vaultwarden ;;
  desk) exec bash "$repo/bellhop/install-desk.sh" ;;
  agent) shift; cmd_agent "$@" ;;
  claude) shift; need_python 9; exec python3 -I "$repo/agents/claude-setup.py" --repo "$repo" "$@" ;;
  uninstall-client) shift; cmd_uninstall_client "$@" ;;
  doctor) cmd_doctor ;;
  guidance) cat "$repo/agents/credential-rules.md" ;;
  update|version-tools) shift; exec bash "$repo/client/install-version-tools" "$@" ;;
  version|--version) echo "valetkey $version" ;;
  proxmox) shift; cmd_proxmox "$@" ;;
  "") if { : <"$tty"; } 2>/dev/null; then cmd_menu; else usage; fi ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
