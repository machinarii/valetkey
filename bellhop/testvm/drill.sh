#!/bin/bash
# Rehearse bellhop/install-bellhop.sh on a throwaway OrbStack machine, with a fake `tailscale`
# command standing in for the tailnet, then call the bellhop over HTTPS from bellhop-agent.
# Proves the installer, the TLS front door and callers-by-machine-name without a tailnet.
# Needs testvm/setup.sh to have run. The drill machine is deleted at the end (KEEP=1 keeps it).
set -euo pipefail
app=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$app/.." && pwd)
DRILL=bellhop-drill AGENT=bellhop-agent NAME=bellhop.test.ts.net

orb() { if [ "$(uname)" = Linux ]; then mac orb "$@"; else command orb "$@"; fi; }
as_root() { orb -m "$1" -u root bash -s; }
as_user() { orb -m "$1" bash -s; }
address() { orb list | awk -v n="$1" '$1 == n { print $NF }'; }

address "$DRILL" | grep -q . || orb create --isolated --cpus 1 --memory 1G debian:trixie "$DRILL"
drill_ip=$(address "$DRILL") agent_ip=$(address "$AGENT")
[ -n "$agent_ip" ] || { echo "run testvm/setup.sh first" >&2; exit 1; }

echo "== drill machine: fake tailscale, then the real installer"
{ echo 'rm -rf /tmp/valetkey; mkdir -p /tmp/valetkey; base64 -d <<"B64" | tar -xz -C /tmp/valetkey'
  tar -C "$repo" --exclude __pycache__ -cz bellhop broker/brokerlib broker/broker_front.py broker/bin/valetkey-broker-cert \
    broker/systemd | base64
  echo B64; } | as_root "$DRILL"
as_root "$DRILL" <<EOF
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3 jq curl openssl ca-certificates >/dev/null
printf 'NAME=$NAME\nIP=$drill_ip\nPEERS="$drill_ip=bellhop $agent_ip=$AGENT"\n' > /etc/bellhop-drill.env
install -m 755 /tmp/valetkey/bellhop/fakes/tailscale-drill /usr/local/bin/tailscale
printf '[Service]\nExecStart=/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n' > /etc/systemd/system/tailscaled.service
grep -q "$NAME" /etc/hosts || echo "$drill_ip $NAME" >> /etc/hosts
bash /tmp/valetkey/bellhop/install-bellhop.sh | sed 's/^/  /'
# A fake tool with a synthetic login, listed for the agent machine by its machine name.
install -m 755 /tmp/valetkey/bellhop/fakes/fakefield /usr/local/bin/fakefield
bellhophome=\$(getent passwd bellhop | cut -d: -f6)
install -d -o bellhop -g bellhop -m 700 \$bellhophome/.config \$bellhophome/.config/fakefield
(umask 077; echo "synthetic-login-drill" > \$bellhophome/.config/fakefield/credentials.json)
chown bellhop:bellhop \$bellhophome/.config/fakefield/credentials.json
grep -q fakefield /etc/bellhop/bellhop.toml || cat >> /etc/bellhop/bellhop.toml <<TOML

[tool.fakefield]
command = "/usr/local/bin/fakefield"
callers = ["$AGENT"]
allow = ["whoami", "upload"]
TOML
systemctl restart bellhop
sleep 1
echo "  as itself (machine name bellhop, not a listed caller): \$(curl -s -m 10 -X POST -d '{"args":["whoami"]}' https://$NAME/bellhop/run/fakefield)"
EOF

echo "== agent machine: trust the drill certificate, then use the tool over HTTPS"
cert=$(echo 'cat /usr/local/share/ca-certificates/bellhop-drill.crt' | as_root "$DRILL")
as_root "$AGENT" <<EOF
grep -q "$NAME" /etc/hosts || echo "$drill_ip $NAME" >> /etc/hosts
cat > /usr/local/share/ca-certificates/bellhop-drill.crt <<'CRT'
$cert
CRT
update-ca-certificates >/dev/null 2>&1
EOF
as_user "$AGENT" <<EOF
cd ~/bellhop-test
export BELLHOP_URL=https://$NAME/bellhop
show() { printf '  %-44s %s\n' "\$1" "\$2"; }
show "tool over HTTPS, caller named by machine:"  "\$(bellhop fakefield whoami 2>&1)"
show "file sent over HTTPS:"                      "\$(bellhop fakefield upload scene.png 2>&1 | cut -c1-40)"
show "subcommand not on the list:"                "\$(bellhop fakefield auth token 2>&1 | cut -c1-60)"
show "allowed here:"                              "\$(valetkey-bellhop --list 2>&1 | tr '\n' ' ')"
show "plain HTTP straight to the bellhop port:"      "\$(BELLHOP_URL=http://$drill_ip:8460 bellhop fakefield whoami 2>&1 | cut -c1-60)"
EOF

echo "== clean up"
as_root "$AGENT" <<EOF
sed -i "/$NAME/d" /etc/hosts
rm -f /usr/local/share/ca-certificates/bellhop-drill.crt && update-ca-certificates >/dev/null 2>&1
EOF
if [ "${KEEP:-}" = 1 ]; then echo "kept $DRILL"; else orb delete -f "$DRILL" && echo "deleted $DRILL"; fi
