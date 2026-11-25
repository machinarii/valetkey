#!/bin/bash
# Run as root on the bellhop machine, from a copy of this repo. Installs the bellhop behind
# valetkey's TLS front door (same front door and certificate renewal as the broker). Safe to
# re-run. It installs no tool and no login: the owner adds those afterwards.
# Override with env vars: BELLHOP_HOSTNAME (default desk), BELLHOP_TAGS (default tag:desk).
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
lib=/usr/local/libexec/relay
BELLHOP_HOSTNAME=${BELLHOP_HOSTNAME:-desk}
BELLHOP_TAGS=${BELLHOP_TAGS:-tag:desk}
ok() { echo "ok    $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "run with sudo"
command -v systemctl >/dev/null || fail "Linux with systemd only"
if command -v claude >/dev/null || command -v codex >/dev/null; then
  fail "an agent is installed on this machine; the bellhop must not share a machine with one"
fi

export DEBIAN_FRONTEND=noninteractive
need=()
for c in python3 jq curl; do command -v "$c" >/dev/null || need+=("$c"); done
[ ${#need[@]} = 0 ] || { apt-get update -qq && apt-get install -y -qq "${need[@]}" >/dev/null; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "python3 3.11 or later is needed"

# Tailscale, installed and joined the same way broker/deps.sh does it.
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled >/dev/null
if ! tailscale status >/dev/null 2>&1; then
  echo
  echo ">>> Open the login URL below in your browser to add the bellhop to the tailnet."
  tailscale up --hostname="$BELLHOP_HOSTNAME" --advertise-tags="$BELLHOP_TAGS"
fi
dns=$(tailscale status --json | jq -r '.Self.DNSName // empty' | sed 's/\.$//')
[ -n "$dns" ] || fail "no tailnet name for this machine"
ok "tailnet name $dns"

bash "$repo/client/install-version-tools"

# desk: owns the tool logins and runs the tools. relay-front: holds only the TLS key.
id desk >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin desk
id relay-front >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin relay-front
deskhome=$(getent passwd desk | cut -d: -f6)

install -d "$lib/relaylib" /etc/bellhop
install -m 644 "$repo"/relay/relaylib/*.py "$lib/relaylib/"
install -m 755 "$repo/relay/relay_front.py" "$repo/bellhop/desk.py" "$repo/broker/bin/relay-cert" "$lib/"
install -m 644 "$repo/bellhop/desk.example.toml" /etc/bellhop/desk.example.toml
# valetkey-bellhop: the bellhop's own commands (--requests, --done ID) without the long path.
printf '#!/bin/sh\nexec /usr/bin/python3 -I %s/desk.py --config /etc/bellhop/desk.toml "$@"\n' "$lib" > /usr/local/sbin/valetkey-bellhop
chmod 755 /usr/local/sbin/valetkey-bellhop
rm -f /usr/local/sbin/valetkey-desk  # this command's name before 0.4
install -d -o desk -g desk -m 700 /var/lib/bellhop /etc/bellhop/keys
if [ ! -f /etc/bellhop/desk.toml ]; then
  cat > /etc/bellhop/desk.toml <<'EOF'
# bellhop. Nothing is listed yet, so nothing can be run. Copy entries from desk.example.toml,
# then: systemctl restart bellhop-desk
[desk]
listen = "127.0.0.1:8460"
identity = "tailscale"
spool = "/var/lib/bellhop"
EOF
fi
python3 -I "$lib/desk.py" --config /etc/bellhop/desk.toml --requests >/dev/null || fail "/etc/bellhop/desk.toml does not load"
ok "bellhop code and configuration"

install -m 644 "$repo"/broker/systemd/relay-front.service "$repo"/broker/systemd/relay-cert.service \
  "$repo"/broker/systemd/relay-cert.timer /etc/systemd/system/
install -d /etc/systemd/system/relay-front.service.d
cat > /etc/systemd/system/relay-front.service.d/desk.conf <<EOF
# This front door has one backend: the bellhop. Callers use https://$dns/desk/...
[Unit]
After=bellhop-desk.service
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -I $lib/relay_front.py --backend desk=127.0.0.1:8460 --tls-dir /var/lib/relay/tls
EOF
cat > /etc/systemd/system/bellhop-desk.service <<EOF
# Installed by bellhop/install-bellhop.sh. Runs listed tools as the desk user, with that user's logins.
[Unit]
Description=bellhop: runs listed tools and forwards listed MCP servers
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
User=desk
Group=desk
WorkingDirectory=$lib
ExecStart=/usr/bin/python3 -I $lib/desk.py --config /etc/bellhop/desk.toml
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/bellhop $deskhome
PrivateTmp=yes
# Logins pass through this process and the tools it starts: never into a core file on disk.
LimitCORE=0
UMask=0077
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
"$lib/relay-cert" || fail "tailscale cert (turn on HTTPS Certificates in the tailnet admin console, then re-run)"
systemctl enable -q bellhop-desk relay-front relay-cert.timer
systemctl restart bellhop-desk relay-front
systemctl start relay-cert.timer
sleep 2
for u in bellhop-desk relay-front; do
  [ "$(systemctl is-active "$u")" = active ] || fail "$u is not running: journalctl -u $u -n 20"
done
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$dns/desk/tools" || true)
[ "$code" = 200 ] || fail "https://$dns/desk/tools answered ${code:-nothing}"
ok "bellhop is up at https://$dns/desk (nothing listed yet)"

cat <<EOF

Next, on this machine:
  1. Install each tool that needs a login, then sign it in as the desk user:
       sudo -u desk -H <tool> <its sign-in command>
     Keys for MCP servers go in /etc/bellhop/keys/<name> (one line, owner desk, mode 600).
  2. List each tool and MCP server in /etc/bellhop/desk.toml (see desk.example.toml), naming the
     agent machines that may use it, then: systemctl restart bellhop-desk
  3. In the tailnet policy, let agent machines reach this machine on tcp:443 and nothing else.
Requests from agents:  valetkey-bellhop --requests
EOF
