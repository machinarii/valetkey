#!/bin/bash
# valetkey broker setup. Run as root on the broker (Debian 12+/Ubuntu 24.04+ with systemd and a
# TPM or vTPM for systemd-creds), from a copy of the repo, after broker/deps.sh:
#   sudo ./install.sh broker            (or: sudo bash broker/setup.sh)
# Idempotent: re-run after pulling new code. Never overwrites /etc/relay/*.toml or vault.env.
# Prints ok/FAIL/skip per step, names only, never ids or values.
#
# Installs:
#   users relay-local, relay-infra, relay-entry  (vault access; homes /var/lib/relay-<name>, 700)
#   user  relay-front                            (no vault access; reads /var/lib/relay/tls)
#   /usr/local/libexec/relay/   relaylib/, relay_{read,entry,front}.py, entry-page.html,
#                               gen-fnox-config.py, vault-info.py, with-bw-session, relay-cert
#   /usr/local/libexec/relay/bin/  relay-login, relay-check, relay-routes, relay-update, relay-approval
#                                  (run as: valetkey-relay login|check|routes|update|approval)
#   /etc/relay/{routes,entry}.toml and vault.env (only if absent)
#   /etc/credstore.encrypted/relay-loopback (generated once)
#   relay-{local,infra,entry,front}.service, relay-cert.{service,timer}
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
src=$repo/relay
conf_src=$repo/config
libexec=/usr/local/libexec/relay
credstore=/etc/credstore.encrypted
fnox=${FNOX_BIN:-/usr/local/bin/fnox}

ok()   { echo "ok    $*"; }
skip() { echo "skip  $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }
step() { local what=$1; shift; if "$@"; then ok "$what"; else fail "$what"; fi; }

# --- preconditions ---
[ -f "$src/relay_read.py" ] && [ -d "$here/systemd" ] || fail "run from the repo: sudo ./install.sh broker"
for c in bw tailscale jq systemd-creds systemd-run; do
  command -v "$c" >/dev/null || fail "$c missing: run sudo ./install.sh broker --with-deps"
done
[ -x "$fnox" ] || fail "$fnox missing: run sudo ./install.sh broker --with-deps"
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || fail "/usr/bin/python3 >= 3.11 needed"
tailscale status >/dev/null 2>&1 || fail "tailscale is not up: tailscale up --hostname=broker --advertise-tags=tag:broker"
ok "preconditions (bw, fnox, tailscale, jq, python3 >= 3.11)"

# --- users and directories ---
mkuser() {  # name home
  id "$1" >/dev/null 2>&1 || useradd --system --user-group --no-create-home \
    --home-dir "$2" --shell /usr/sbin/nologin "$1"
}
for n in local infra entry; do
  u=relay-$n h=/var/lib/relay-$n
  step "user $u" mkuser "$u" "$h"
  install -d -o "$u" -g "$u" -m 700 "$h" "$h/bw-cli"
  [ "$n" = entry ] || install -d -o "$u" -g "$u" -m 700 "$h/fnox-global"
done
step "user relay-front" mkuser relay-front /var/lib/relay
install -d -o root -g root -m 755 /var/lib/relay
install -d -o root -g relay-front -m 750 /var/lib/relay/tls
install -d -o root -g root -m 700 "$credstore"
ok "directories /var/lib/relay-{local,infra,entry} (700), /var/lib/relay/tls (root:relay-front 750)"

# --- code ---
install_code() {
  install -d -m 755 "$libexec" "$libexec/relaylib" "$libexec/bin"
  rm -rf "$libexec/__pycache__" "$libexec/relaylib/__pycache__"
  install -m 644 "$src"/relaylib/*.py "$libexec/relaylib/"
  install -m 644 "$src/relay_read.py" "$src/relay_entry.py" "$src/relay_front.py" "$src/approval_worker.py" \
    "$src/entry-page.html" "$src/tools/gen-fnox-config.py" "$src/tools/vault-info.py" "$libexec/"
  install -m 755 "$here/bin/with-bw-session" "$here/bin/relay-cert" "$libexec/"
  # Run through `valetkey-relay <word>`; they are not on the PATH.
  install -m 755 "$here/bin/relay-login" "$here/bin/relay-check" "$here/bin/relay-routes" \
    "$here/bin/relay-update" "$here/bin/relay-approval" "$libexec/bin/"
  for old in relay-login relay-check relay-routes relay-update relay-approval; do  # their place before
    rm -f "/usr/local/sbin/${old:?}"
  done
  bash "$repo/client/install-version-tools" || return 1
  install -m 644 "$repo/VERSION" "$libexec/VERSION"
  install -m 644 "$here/CONFIG_EPOCH" "$libexec/CONFIG_EPOCH"
  # Which commit this is is only known to relay-update, which writes COMMIT after this script.
  rm -f "$libexec/COMMIT"
  # The units call /usr/local/bin/fnox; point it at FNOX_BIN when fnox lives elsewhere.
  [ "$fnox" = /usr/local/bin/fnox ] || ln -sf "$fnox" /usr/local/bin/fnox
}
step "valetkey $(cat "$repo/VERSION") in $libexec; valetkey-relay update|routes|login|check|approval" install_code

# --- configuration (the owner's edits are never overwritten) ---
install -d -m 755 /etc/relay /etc/relay/approved-routes.d
for f in routes.toml entry.toml vault.env; do
  case $f in vault.env) example=vault.example.env ;; *) example=${f%.toml}.example.toml ;; esac
  if [ -f "/etc/relay/$f" ]; then
    ok "kept existing /etc/relay/$f"
  else
    step "/etc/relay/$f from config/$example (edit it for your machines)" \
      install -m 644 "$conf_src/$example" "/etc/relay/$f"
  fi
done
# entry.toml: fill in this broker's tailnet name if it still has the example one.
dns=$(tailscale status --json | jq -r '.Self.DNSName // empty' | sed 's/\.$//')
if [ -n "$dns" ] && grep -q '^tailnet_name *= *"broker.example.ts.net"' /etc/relay/entry.toml; then
  sed -i "s|^tailnet_name *= *\"broker.example.ts.net\"|tailnet_name = \"$dns\"|" /etc/relay/entry.toml
  ok "/etc/relay/entry.toml tailnet_name = $dns"
fi
step "routes and entry config valid" "$libexec/bin/relay-routes" check

# --- loopback secret shared by the three services (32 random bytes, hex), generated once ---
for name in relay-loopback relay-approval-front; do
  if [ -f "$credstore/$name" ]; then
    ok "kept existing $credstore/$name"
  else
    /usr/bin/python3 -I -c 'import secrets, sys; sys.stdout.write(secrets.token_hex(32))' \
      | systemd-creds encrypt --name="$name" - "$credstore/$name"
    chmod 600 "$credstore/$name"
    ok "generated $credstore/$name"
  fi
done

# --- units ---
step "units in /etc/systemd/system" install -m 644 "$here"/systemd/relay-*.service \
  "$here"/systemd/relay-cert.timer /etc/systemd/system/
install -d -m 755 /etc/systemd/system/relay-front.service.d
install -m 644 "$here/systemd/approval-front.conf" /etc/systemd/system/relay-front.service.d/approval-front.conf
systemctl daemon-reload
if [ -f "$credstore/relay-approval-key" ]; then
  step "restart enrolled approval worker" systemctl restart relay-approval.service
fi
for u in relay-local relay-infra relay-entry relay-front; do
  systemctl enable "$u.service" >/dev/null 2>&1 || fail "enable $u"
done
ok "enabled relay-local, relay-infra, relay-entry, relay-front"

# --- TLS certificate (needs HTTPS Certificates on in the tailnet) + weekly renewal ---
cert_ok=yes
if "$libexec/relay-cert" >/dev/null; then
  ok "certificate in /var/lib/relay/tls"
else
  cert_ok=no
  echo "FAIL  tailscale cert (turn on HTTPS Certificates in the admin console, then re-run)" >&2
fi
step "relay-cert.timer (weekly)" systemctl enable --now relay-cert.timer --quiet

# --- start what has credentials ---
rc=0
"$libexec/bin/relay-routes" install || rc=1   # relay-local, relay-infra: api.toml + restart
if [ -f "$credstore/bw-unlock-entry" ] && [ -f /var/lib/relay-entry/org.json ]; then
  systemctl restart relay-entry.service
  sleep 3
  if systemctl is-active --quiet relay-entry.service; then ok "relay-entry running"
  else echo "FAIL  relay-entry not running; see: journalctl -u relay-entry -n 30" >&2; rc=1; fi
else
  skip "relay-entry: run valetkey-relay login entry <email> first"
fi
if [ "$cert_ok" = yes ]; then
  systemctl restart relay-front.service
  sleep 2
  if systemctl is-active --quiet relay-front.service; then ok "relay-front running on https://$dns"
  else echo "FAIL  relay-front not running; see: journalctl -u relay-front -n 30" >&2; rc=1; fi
else
  skip "relay-front: no certificate yet"; rc=1
fi

echo
echo "Next: edit /etc/relay/routes.toml, entry.toml and vault.env for your machines, then"
echo "      sudo valetkey-relay login local <email>; sudo valetkey-relay login infra <email>; sudo valetkey-relay login entry <email>"
echo "      sudo valetkey-relay check"
exit $rc
