#!/bin/bash
# Run as root on the bellhop machine, after install-bellhop.sh. Installs a starter set of tools that
# hold a login and lists them in /etc/bellhop/bellhop.toml for the named agent machines. Safe to
# re-run. It signs nothing in: the sign-in steps are printed at the end, for the owner.
#
#   bellhop-tools.sh CALLERS [TOOL ...]
#     CALLERS  comma-separated Tailscale machine names of the agent machines, e.g. agent-dev,agent-ci
#     TOOL     higgsfield vercel stripe (default: all three)
#
# These three are examples of the pattern, not a recommendation. An MCP server with a fixed key
# does not belong here: give it a broker [api.*] route instead.
#
# The allow lists below are a cautious start: read and create, nothing that prints, exports or
# changes a login. Read them before widening them.
set -euo pipefail
conf=/etc/bellhop/bellhop.toml
lib=/usr/local/libexec/broker
ok() { echo "ok    $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "run with sudo"
[ -f "$conf" ] || fail "no $conf: run install-bellhop.sh first"
callers=${1:-}
[[ $callers =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]] || fail "usage: bellhop-tools.sh CALLERS [TOOL ...]   CALLERS like agent-dev,agent-ci"
shift
list="[\"${callers//,/\", \"}\"]"
tools=("$@")
[ ${#tools[@]} -gt 0 ] || tools=(higgsfield vercel stripe)
export DEBIAN_FRONTEND=noninteractive
cp -p "$conf" "$conf.before-tools"
steps=()

add() {  # add KIND NAME, entry on stdin: appended once, never rewritten
  if grep -q "^\[$1\.$2\]" "$conf"; then
    cat >/dev/null
    ok "$1.$2 is already listed (left as it is)"
  else
    { echo; cat; } >> "$conf"
    ok "$1.$2 listed for $callers"
  fi
}

npm_tool() {  # npm_tool COMMAND PACKAGE VERSION
  command -v npm >/dev/null || { apt-get update -qq && apt-get install -y -qq nodejs npm >/dev/null; }
  command -v "$1" >/dev/null || npm install -g --silent "$2@$3" >/dev/null
  command -v "$1" >/dev/null || fail "$1 did not install"
}

# These machines may also file install requests (server.py --requests).
grep -q '^requesters' "$conf" || sed -i "/^\[bellhop\]/a requesters = $list" "$conf"

for tool in "${tools[@]}"; do
  case $tool in
    higgsfield)
      # The vendor's installer checks the download against the release's checksum file.
      command -v higgsfield >/dev/null || curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh \
        | sh -s -- --tag v1.1.24 --no-hf >/dev/null
      add tool higgsfield <<EOF
[tool.higgsfield]
command = "$(command -v higgsfield)"
callers = $list
# Left out on purpose: auth (prints and changes the login), workspace (billing account),
# website (builds from project files).
allow = ["account", "generate", "model", "preset", "upload", "workflow", "voices", "soul-id",
         "marketing-studio", "marketplace-cards", "product-photoshoot", "version"]
timeout = 900
EOF
      steps+=("higgsfield (browser sign-in in one terminal, then pick the billing workspace):
      sudo -u bellhop -H higgsfield auth login --port 8765 & sleep 4; echo; read -rp \"Paste the localhost address here, then Enter: \" u; curl -s \"\$u\"; echo; wait
        Open the link it prints and approve. Your browser then fails to load a page at
        http://localhost:8765/...  Copy that whole address and paste it at the prompt.
      sudo -u bellhop -H higgsfield workspace list
      sudo -u bellhop -H higgsfield workspace set <workspace id>") ;;
    vercel)
      npm_tool vercel vercel 66.5.0
      add tool vercel <<EOF
[tool.vercel]
command = "$(command -v vercel)"
callers = $list
# Read-only. Left out on purpose: env and pull (print variable values), deploy, build, dev and
# link (need project files), curl (protection bypass), and everything that changes a project.
allow = ["inspect", "list", "ls", "logs", "health", "whoami", "project ls", "project list",
         "project inspect", "domains ls", "domains inspect", "alias ls", "certs ls", "dns ls"]
deny = ["--token", "-t", "--debug", "-d", "--cwd", "--local-config", "-A", "--global-config", "-Q"]
EOF
      steps+=("vercel (prints a link and a code to approve in your browser):
      sudo -u bellhop -H vercel login") ;;
    stripe)
      npm_tool stripe @stripe/cli 1.55.14
      add tool stripe <<EOF
[tool.stripe]
command = "$(command -v stripe)"
callers = $list
# Read-only listings. Left out on purpose: config (prints keys), login and logout, get/post/delete
# (free-form requests), listen and trigger (long-running, forward to this machine).
allow = ["customers list", "charges list", "payment_intents list", "products list", "prices list",
         "subscriptions list", "invoices list", "events list", "balance retrieve", "version"]
deny = ["--api-key", "--log-level", "--config", "--project-name"]
EOF
      steps+=("stripe (prints a link and a pairing code to approve in your browser):
      sudo -u bellhop -H stripe login") ;;
    *) fail "unknown tool: $tool (known: higgsfield vercel stripe)" ;;
  esac
done

if ! python3 -I "$lib/server.py" --config "$conf" --requests >/dev/null; then
  cp -p "$conf.before-tools" "$conf"
  fail "the new $conf did not load; the previous one is back in place"
fi
systemctl restart bellhop
sleep 1
[ "$(systemctl is-active bellhop)" = active ] || fail "bellhop is not running: journalctl -u bellhop -n 20"
ok "bellhop restarted; $conf.before-tools holds the previous configuration"

echo
echo "Nothing is signed in yet. On this machine, as the owner:"
for step in "${steps[@]}"; do printf '\n  %s\n' "$step"; done
echo
echo "Then, from an agent machine:  valetkey-bellhop --list"
