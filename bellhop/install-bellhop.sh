#!/bin/bash
# Run as root on the bellhop machine, from a copy of this repo. Installs the bellhop behind
# valetkey's TLS front door (same front door and certificate renewal as the broker). Safe to
# re-run. It installs no tool and no login: the owner adds those afterwards.
# Override with env vars: BELLHOP_HOSTNAME (default bellhop), BELLHOP_TAGS (default tag:bellhop).
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
lib=/usr/local/libexec/valetkey-broker
BELLHOP_HOSTNAME=${BELLHOP_HOSTNAME:-bellhop}
BELLHOP_TAGS=${BELLHOP_TAGS:-tag:bellhop}
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

# A bellhop installed before 0.5 used the name "desk" (its user, service, configuration and address)
# and the front door's earlier names, "relay" and (in 0.5.0) "broker". Move them, once. The tool logins stay with the same
# user id. There is no automatic way back: snapshot or back up the machine before updating.
migrate_names() {
  local old_home new_home f
  if id desk >/dev/null 2>&1 && ! id bellhop >/dev/null 2>&1; then
    old_home=$(getent passwd desk | cut -d: -f6)
    new_home=$(dirname "$old_home")/bellhop
    [ ! -e "$new_home" ] || fail "$new_home already exists; move it away and run this again"
    systemctl stop bellhop-desk.service 2>/dev/null || true
    usermod -l bellhop -d "$new_home" -m desk
    if getent group desk >/dev/null; then groupmod -n bellhop desk; fi
    ln -s "$new_home" "$old_home"  # a tool that recorded its old folder still finds it
    ok "user desk is now bellhop (same id, same logins); its home is $new_home"
  fi
  if id relay-front >/dev/null 2>&1 && ! id valetkey-broker-front >/dev/null 2>&1; then
    systemctl stop relay-front.service 2>/dev/null || true
    usermod -l valetkey-broker-front relay-front
    if getent group relay-front >/dev/null; then groupmod -n valetkey-broker-front relay-front; fi
    ok "user relay-front is now valetkey-broker-front"
  fi
  if id broker-front >/dev/null 2>&1 && ! id valetkey-broker-front >/dev/null 2>&1; then
    systemctl stop broker-front.service 2>/dev/null || true
    usermod -l valetkey-broker-front broker-front
    if getent group broker-front >/dev/null; then groupmod -n valetkey-broker-front broker-front; fi
    ok "user broker-front is now valetkey-broker-front"
  fi
  if [ -f /etc/bellhop/desk.toml ] && [ ! -e /etc/bellhop/bellhop.toml ]; then
    mv /etc/bellhop/desk.toml /etc/bellhop/bellhop.toml
    sed -i 's/^\[desk\]/[bellhop]/' /etc/bellhop/bellhop.toml
    # Agent machines set up before 0.5 still call https://<this machine>/desk.
    echo "Delete this file and run install-bellhop.sh again once every agent machine uses .../bellhop" > /etc/bellhop/old-address
    ok "/etc/bellhop/desk.toml is now /etc/bellhop/bellhop.toml"
  fi
  rm -f /etc/bellhop/desk.example.toml
  # The certificate folder: under relay before 0.5, under /var/lib/broker in 0.5.0.
  for f in /var/lib/relay/tls /var/lib/broker/tls; do
    if [ -d "$f" ] && [ ! -e /var/lib/valetkey-broker-front/tls ]; then
      install -d -m 755 /var/lib/valetkey-broker-front
      mv "$f" /var/lib/valetkey-broker-front/tls
      rmdir "$(dirname "$f")" 2>/dev/null || true
    fi
  done
  for f in bellhop-desk.service relay-front.service relay-cert.service relay-cert.timer \
           broker-front.service broker-cert.service broker-cert.timer; do
    if [ -e "/etc/systemd/system/$f" ]; then
      systemctl disable --now "$f" 2>/dev/null || true
      rm -f "/etc/systemd/system/$f"
    fi
  done
  rm -rf /etc/systemd/system/relay-front.service.d /etc/systemd/system/broker-front.service.d /usr/local/libexec/relay
  # 0.5.0 put the front door in a folder called just "broker": remove what it put there, nothing else.
  old=/usr/local/libexec/broker
  rm -rf "${old:?}/brokerlib" "${old:?}/__pycache__"
  rm -f "${old:?}/broker_front.py" "${old:?}/server.py" "${old:?}/broker-cert"
  rmdir "$old" 2>/dev/null || true
}
migrate_names

# bellhop: owns the tool logins and runs the tools. valetkey-broker-front: holds only the TLS key.
id bellhop >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin bellhop
id valetkey-broker-front >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin valetkey-broker-front
bellhophome=$(getent passwd bellhop | cut -d: -f6)

install -d "$lib/brokerlib" /etc/bellhop
install -m 644 "$repo"/broker/brokerlib/*.py "$lib/brokerlib/"
install -m 755 "$repo/broker/broker_front.py" "$repo/bellhop/server.py" "$repo/broker/bin/valetkey-broker-cert" "$lib/"
install -m 644 "$repo/bellhop/bellhop.example.toml" /etc/bellhop/bellhop.example.toml
# valetkey-bellhop: the bellhop's own commands (--requests, --done ID) without the long path.
printf '#!/bin/sh\nexec /usr/bin/python3 -I %s/server.py --config /etc/bellhop/bellhop.toml "$@"\n' "$lib" > /usr/local/sbin/valetkey-bellhop
chmod 755 /usr/local/sbin/valetkey-bellhop
rm -f /usr/local/sbin/valetkey-desk  # this command's name before 0.4
install -d -o bellhop -g bellhop -m 700 /var/lib/bellhop /etc/bellhop/keys
if [ ! -f /etc/bellhop/bellhop.toml ]; then
  cat > /etc/bellhop/bellhop.toml <<'EOF'
# bellhop. Nothing is listed yet, so nothing can be run. Copy entries from bellhop.example.toml,
# then: systemctl restart bellhop
[bellhop]
listen = "127.0.0.1:8460"
identity = "tailscale"
spool = "/var/lib/bellhop"
EOF
fi
python3 -I "$lib/server.py" --config /etc/bellhop/bellhop.toml --requests >/dev/null || fail "/etc/bellhop/bellhop.toml does not load"
ok "bellhop code and configuration"

install -m 644 "$repo"/broker/systemd/valetkey-broker-front.service "$repo"/broker/systemd/valetkey-broker-cert.service \
  "$repo"/broker/systemd/valetkey-broker-cert.timer /etc/systemd/system/
install -d /etc/systemd/system/valetkey-broker-front.service.d
backends="--backend bellhop=127.0.0.1:8460"
if [ -f /etc/bellhop/old-address ]; then  # see migrate_names: the address before 0.5 still answers
  backends="$backends --backend desk=127.0.0.1:8460"
fi
cat > /etc/systemd/system/valetkey-broker-front.service.d/bellhop.conf <<EOF
# This front door has one backend: the bellhop. Callers use https://$dns/bellhop/...
[Unit]
After=bellhop.service
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -I $lib/broker_front.py $backends --tls-dir /var/lib/valetkey-broker-front/tls
EOF
cat > /etc/systemd/system/bellhop.service <<EOF
# Installed by bellhop/install-bellhop.sh. Runs listed tools as the bellhop user, with that user's logins.
[Unit]
Description=bellhop: runs listed tools and forwards listed MCP servers
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
User=bellhop
Group=bellhop
WorkingDirectory=$lib
ExecStart=/usr/bin/python3 -I $lib/server.py --config /etc/bellhop/bellhop.toml
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/bellhop $bellhophome
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
"$lib/valetkey-broker-cert" || fail "tailscale cert (turn on HTTPS Certificates in the tailnet admin console, then re-run)"
systemctl enable -q bellhop valetkey-broker-front valetkey-broker-cert.timer
systemctl restart bellhop valetkey-broker-front
systemctl start valetkey-broker-cert.timer
sleep 2
for u in bellhop valetkey-broker-front; do
  [ "$(systemctl is-active "$u")" = active ] || fail "$u is not running: journalctl -u $u -n 20"
done
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$dns/bellhop/tools" || true)
[ "$code" = 200 ] || fail "https://$dns/bellhop/tools answered ${code:-nothing}"
ok "bellhop is up at https://$dns/bellhop"
if [ -f /etc/bellhop/old-address ]; then
  echo "note  the address from before 0.5, https://$dns/desk, still answers. On each agent machine run the"
  echo "      installer again with --bellhop-url https://$dns/bellhop, then delete /etc/bellhop/old-address"
  echo "      here and run this script again."
fi

cat <<EOF

Next, on this machine:
  1. Install each tool that needs a login, then sign it in as the bellhop user:
       sudo -u bellhop -H <tool> <its sign-in command>
     Keys for MCP servers go in /etc/bellhop/keys/<name> (one line, owner bellhop, mode 600).
  2. List each tool and MCP server in /etc/bellhop/bellhop.toml (see bellhop.example.toml), naming the
     agent machines that may use it, then: systemctl restart bellhop
  3. In the tailnet policy, let agent machines reach this machine on tcp:443 and nothing else.
Requests from agents:  valetkey-bellhop --requests
EOF
