#!/bin/bash
# Build two throwaway OrbStack machines that show bellhop working. Synthetic logins only.
#
#   bellhop-desk    holds a fake tool login and a fake MCP key, runs the bellhop, has no agent
#   bellhop-agent   has the bellhop client (and Claude Code, not signed in); holds no key
#
# Both are isolated machines: no Mac files, no `mac` or `orb` command inside them.
# Run on the Mac, or in an OrbStack machine that has the `mac` command.
#   testvm/setup.sh           create or refresh both machines, then check them
#   testvm/setup.sh delete    remove both
set -euo pipefail
app=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$app/.." && pwd)
DESK=${DESK:-bellhop-desk} AGENT=${AGENT:-bellhop-agent} PORT=8460   # override the names to keep an existing pair

orb() { if [ "$(uname)" = Linux ]; then mac orb "$@"; else command orb "$@"; fi; }
as_root() { orb -m "$1" -u root bash -s; }   # script on stdin
as_user() { orb -m "$1" bash -s; }
# Files go in as text inside the script: the `mac` command rewrites path-like arguments.
unpack() {
  { echo 'rm -rf /opt/bellhop-src; mkdir -p /opt/bellhop-src; base64 -d <<"B64" | tar -x -C /opt/bellhop-src'
    base64; echo B64; } | as_root "$1"
}
address() { orb list | awk -v n="$1" '$1 == n { print $NF }'; }

if [ "${1:-}" = delete ]; then
  orb delete -f "$DESK" "$AGENT"
  exit 0
fi

address "$DESK" | grep -q . || orb create --isolated --cpus 1 --memory 1G debian:trixie "$DESK"
address "$AGENT" | grep -q . || orb create --isolated --cpus 2 --memory 2G debian:trixie "$AGENT"
desk_ip=$(address "$DESK") agent_ip=$(address "$AGENT")
echo "bellhop $desk_ip, agent $agent_ip"

echo "== bellhop: the service, a fake tool and a fake MCP server, each with a synthetic login"
tar -C "$repo" -c bellhop/desk.py bellhop/fakes relay/relaylib/__init__.py relay/relaylib/httpbase.py \
  relay/relaylib/identity.py | unpack "$DESK"
as_root "$DESK" <<EOF
set -eu
export DEBIAN_FRONTEND=noninteractive
command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3 >/dev/null; }
id desk >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin desk
src=/opt/bellhop-src
install -d /opt/bellhop/relaylib /opt/bellhop/fakes /etc/bellhop
install -m 755 \$src/bellhop/desk.py /opt/bellhop/desk.py
install -m 644 \$src/relay/relaylib/*.py /opt/bellhop/relaylib/
install -m 755 \$src/bellhop/fakes/fakemcp.py /opt/bellhop/fakes/fakemcp.py
install -m 755 \$src/bellhop/fakes/fakefield /usr/local/bin/fakefield
random() { head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
deskhome=\$(getent passwd desk | cut -d: -f6)
install -d -o desk -g desk -m 700 \$deskhome/.config \$deskhome/.config/fakefield /etc/bellhop/keys /var/lib/bellhop
login=\$deskhome/.config/fakefield/credentials.json key=/etc/bellhop/keys/fakemcp
[ -s \$login ] || (umask 077; echo "synthetic-login-\$(random)" > \$login)
[ -s \$key ] || (umask 077; echo "Bearer synthetic-\$(random)" > \$key)
chown desk:desk \$login \$key
cat > /etc/bellhop/desk.toml <<TOML
[desk]
listen = "0.0.0.0:$PORT"
identity = "address"   # test network without Tailscale: callers are known by address
spool = "/var/lib/bellhop"

[address]
"$agent_ip" = "$AGENT"

[tool.fakefield]
command = "/usr/local/bin/fakefield"
callers = ["$AGENT"]
allow = ["whoami", "upload", "make", "fail"]
deny = ["--debug"]

[mcp.fakemcp]
upstream = "http://127.0.0.1:8470/mcp"
header = "Authorization"
value_file = "/etc/bellhop/keys/fakemcp"
callers = ["$AGENT"]
TOML
cat > /etc/systemd/system/bellhop-desk.service <<UNIT
[Unit]
Description=bellhop
After=network-online.target bellhop-fakemcp.service
[Service]
User=desk
ExecStart=/usr/bin/python3 -I /opt/bellhop/desk.py --config /etc/bellhop/desk.toml
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/bellhop \$deskhome
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/bellhop-fakemcp.service <<UNIT
[Unit]
Description=fake MCP server that demands a key (test fixture)
[Service]
User=desk
ExecStart=/usr/bin/python3 /opt/bellhop/fakes/fakemcp.py 127.0.0.1:8470 /etc/bellhop/keys/fakemcp
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q bellhop-fakemcp bellhop-desk
systemctl restart bellhop-fakemcp bellhop-desk
sleep 1
systemctl is-active bellhop-fakemcp bellhop-desk
EOF

echo "== agent: the real installer (stand-in, sweep, MCP policy), a test project, Claude Code, no sudo"
keyring=/etc/apt/keyrings/claude-code.asc   # public signing key, copied from this machine if it has one
tar -C "$repo" --exclude .git --exclude __pycache__ -c . $( [ -r "$keyring" ] && echo "-C / ${keyring#/}" ) | unpack "$AGENT"
as_root "$AGENT" <<EOF
set -eu
export DEBIAN_FRONTEND=noninteractive
command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3 ca-certificates >/dev/null; }
user=\$(ls /home | head -1)
rm -f /etc/claude-code/managed-settings.json   # a test machine: let the installer write a fresh policy
bash /opt/bellhop-src/install.sh agent --bellhop-url "http://$desk_ip:$PORT" --tools fakefield --user \$user 2>&1 | grep -E '^(ok|FAIL)' | sed 's/^/  /'
project=\$(getent passwd \$user | cut -d: -f6)/bellhop-test
install -d -o \$user -g \$user \$project
cat > \$project/.mcp.json <<JSON
{ "mcpServers": { "fakemcp": { "type": "http", "url": "http://$desk_ip:$PORT/mcp/fakemcp" } } }
JSON
printf 'a local picture\n' > \$project/scene.png
cat > \$project/README.txt <<TXT
Try these here. The tool and its login live on $DESK; nothing on this machine holds a key.
  fakefield whoami                 runs on the bellhop with the bellhop's login
  fakefield upload scene.png       the file is sent along with the command
  fakefield make result.txt        a file the tool writes comes back here
  fakefield auth token             refused: that subcommand would print the login
  valetkey-bellhop --list                   what the bellhop lets this machine use
  claude                           after signing in: the fakemcp server offers a whoami tool
TXT
chown -R \$user:\$user \$project
if [ -r /opt/bellhop-src/${keyring#/} ] && ! command -v claude >/dev/null; then
  install -D -m 644 /opt/bellhop-src/${keyring#/} $keyring
  echo "deb [signed-by=$keyring] https://downloads.claude.ai/claude-code/apt/latest latest main" \
    > /etc/apt/sources.list.d/claude-code.list
  apt-get update -qq && apt-get install -y -qq claude-code >/dev/null || echo "Claude Code not installed (package step failed)"
fi
command -v claude >/dev/null && echo "Claude Code installed, not signed in" || echo "Claude Code not installed"
# An agent that can become root can change anything on its own machine. Take that away.
rm -f /etc/sudoers.d/orbstack
gpasswd -d \$user sudo >/dev/null 2>&1 || true
EOF

echo "== check, from the agent machine as its ordinary user"
as_user "$AGENT" <<EOF
cd ~/bellhop-test
show() { printf '%-46s %s\n' "\$1" "\$2"; }
show "tool runs with the bellhop login:"        "\$(fakefield whoami 2>&1)"
show "file travels with the command:"        "\$(fakefield upload scene.png 2>&1 | cut -c1-40)"
rm -f result.txt; fakefield make result.txt >/dev/null 2>&1
show "file the tool wrote came back:"        "\$(cat result.txt 2>&1)"
show "login-printing subcommand:"            "\$(fakefield auth token 2>&1 | cut -c1-60)"
show "path on the bellhop as an argument:"      "\$(fakefield upload /etc/bellhop/keys/fakemcp 2>&1 | cut -c1-60)"
show "allowed here:"                         "\$(valetkey-bellhop --list 2>&1 | tr '\n' ' ')"
ask='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami"}}'
show "MCP through the bellhop, no key here:"    "\$(python3 -c "
import urllib.request,json,sys
r=urllib.request.urlopen(urllib.request.Request('http://$desk_ip:$PORT/mcp/fakemcp',data=sys.argv[1].encode()))
print(json.load(r)['result']['content'][0]['text'])" "\$ask" 2>&1 | tail -1)"
show "MCP server reached directly:"          "\$(python3 -c "
import urllib.request
try: urllib.request.urlopen('http://$desk_ip:8470/mcp', data=b'{}', timeout=5); print('REACHABLE')
except Exception as e: print('blocked (' + type(e).__name__ + ')')" 2>&1)"
show "synthetic login anywhere on this machine:" "\$(grep -rIl 'synthetic-' /home /etc /opt /usr/local /tmp 2>/dev/null | grep -v bellhop-src | head -3 | tr '\n' ' ' | grep . || echo none)"
show "install request filed with the bellhop:"  "\$(valetkey-bellhop --request mcp buffer https://mcp.buffer.com/mcp schedule posts 2>&1 | cut -c1-60)"
show "MCP server added outside the bellhop:"    "\$(claude mcp add --transport http rogue https://mcp.example.com/mcp 2>&1 | tail -1 | cut -c1-70)"
show "sudo for the agent's user:"            "\$(sudo -n true 2>/dev/null && echo STILL ALLOWED || echo removed)"
show "reach into the Mac (mac command):"     "\$(timeout 10 mac echo reached 2>/dev/null | grep -q reached && echo WORKS || echo no)"
EOF

echo "== sweep: plant a fake login and a new tool on the agent machine, then run the check as root"
as_user "$AGENT" <<'PLANT'
mkdir -p ~/.config/higgsfield ~/.local/bin
echo "synthetic-login-planted-for-the-test" > ~/.config/higgsfield/credentials.json
printf 'API_KEY=synthetic-key-planted-for-test\n' > ~/bellhop-test/.env
printf '#!/bin/sh\n' > ~/.local/bin/newtool && chmod +x ~/.local/bin/newtool
PLANT
echo '/usr/local/sbin/valetkey-sweep | sed "s/^/  /"; systemctl list-timers bellhop-sweep.timer --no-pager | sed -n 2p | cut -c1-70' | as_root "$AGENT"
as_user "$AGENT" <<'CLEAN'
rm -rf ~/.config/higgsfield ~/bellhop-test/.env ~/.local/bin/newtool
CLEAN
echo '/usr/local/sbin/valetkey-sweep | sed "s/^/  after cleanup: /"' | as_root "$AGENT"

echo "== bellhop: what the owner sees"
echo 'python3 -I /opt/bellhop/desk.py --requests | sed "s/^/  /"; : > /var/lib/bellhop/requests.jsonl' | as_root "$DESK"   # shown, then cleared: it was a test
