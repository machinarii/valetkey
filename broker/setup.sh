#!/bin/bash
# valetkey broker setup. Run as root on the broker (Debian 12+/Ubuntu 24.04+ with systemd and a
# TPM or vTPM for systemd-creds), from a copy of the repo, after broker/deps.sh:
#   sudo ./install.sh broker            (or: sudo bash broker/setup.sh)
# Idempotent: re-run after pulling new code. Never overwrites /etc/valetkey-broker/*.toml or vault.env.
# Prints ok/FAIL/skip per step, names only, never ids or values.
#
# Installs:
#   users valetkey-broker-local, valetkey-broker-infra, valetkey-broker-entry  (vault access; homes /var/lib/valetkey-broker-<name>, 700)
#   user  valetkey-broker-front                            (no vault access; reads /var/lib/valetkey-broker-front/tls)
#   /usr/local/libexec/valetkey-broker/   brokerlib/, broker_{read,entry,front}.py, entry-page.html,
#                               gen-fnox-config.py, vault-info.py, valetkey-broker-cert
#   /usr/local/libexec/valetkey-broker/bin/  valetkey-broker-login, valetkey-broker-check, valetkey-broker-routes, valetkey-broker-update, valetkey-broker-approval,
#                                  with-bw-session
#                                  (run as: valetkey-broker login|check|routes|update|approval)
#   /etc/valetkey-broker/{routes,entry}.toml and vault.env (only if absent)
#   /etc/credstore.encrypted/valetkey-broker-loopback (generated once)
#   valetkey-broker-{local,infra,entry,front}.service, valetkey-broker-cert.{service,timer}
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
src=$repo/broker
conf_src=$repo/config
libexec=/usr/local/libexec/valetkey-broker
credstore=/etc/credstore.encrypted
fnox=${FNOX_BIN:-/usr/local/bin/fnox}

ok()   { echo "ok    $*"; }
skip() { echo "skip  $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }
step() { local what=$1; shift; if "$@"; then ok "$what"; else fail "$what"; fi; }

# --- preconditions ---
[ -f "$src/broker_read.py" ] && [ -d "$here/systemd" ] || fail "run from the repo: sudo ./install.sh broker"
for c in bw tailscale jq systemd-creds systemd-run; do
  command -v "$c" >/dev/null || fail "$c missing: run sudo ./install.sh broker --with-deps"
done
[ -x "$fnox" ] || fail "$fnox missing: run sudo ./install.sh broker --with-deps"
/usr/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || fail "/usr/bin/python3 >= 3.11 needed"
tailscale status >/dev/null 2>&1 || fail "tailscale is not up: tailscale up --hostname=broker --advertise-tags=tag:broker"
ok "preconditions (bw, fnox, tailscale, jq, python3 >= 3.11)"

# --- a broker from before 0.5: move its names on disk from relay to valetkey-broker (no-op otherwise) ---
rc=0
bash "$here/migrate-names.sh" || rc=$?
if [ "$rc" = 3 ]; then  # a check failed before anything was touched
  echo "FAIL  The old services keep running. An updater from 0.4 or earlier will still say it installed" >&2
  echo "      this version. After fixing what is named above, run:" >&2
  echo "        sudo rm /usr/local/libexec/relay/COMMIT" >&2
  echo "      and update again." >&2
  exit 1
elif [ "$rc" != 0 ]; then
  echo "FAIL  The move from relay to valetkey-broker stopped part-way, and the broker's services are down." >&2
  echo "      Run the update again: it continues where it stopped. Or restore the snapshot or backup." >&2
  exit 1
fi

# --- users and directories ---
mkuser() {  # name home
  id "$1" >/dev/null 2>&1 || useradd --system --user-group --no-create-home \
    --home-dir "$2" --shell /usr/sbin/nologin "$1"
}
for n in local infra entry; do
  u=valetkey-broker-$n h=/var/lib/valetkey-broker-$n
  step "user $u" mkuser "$u" "$h"
  install -d -o "$u" -g "$u" -m 700 "$h" "$h/bw-cli"
  [ "$n" = entry ] || install -d -o "$u" -g "$u" -m 700 "$h/fnox-global"
done
step "user valetkey-broker-front" mkuser valetkey-broker-front /var/lib/valetkey-broker-front
install -d -o root -g root -m 755 /var/lib/valetkey-broker-front
install -d -o root -g valetkey-broker-front -m 750 /var/lib/valetkey-broker-front/tls
install -d -o root -g root -m 700 "$credstore"
ok "directories /var/lib/valetkey-broker-{local,infra,entry} (700), /var/lib/valetkey-broker-front/tls (root:valetkey-broker-front 750)"

# --- code ---
install_code() {
  install -d -m 755 "$libexec" "$libexec/brokerlib" "$libexec/bin"
  rm -rf "$libexec/__pycache__" "$libexec/brokerlib/__pycache__"
  install -m 644 "$src"/brokerlib/*.py "$libexec/brokerlib/"
  install -m 644 "$src/broker_read.py" "$src/broker_entry.py" "$src/broker_front.py" "$src/approval_worker.py" \
    "$src/entry-page.html" "$src/tools/gen-fnox-config.py" "$src/tools/vault-info.py" "$libexec/"
  install -m 755 "$here/bin/valetkey-broker-cert" "$libexec/"
  # Run through `valetkey-broker <word>`; they are not on the PATH.
  install -m 755 "$here/bin/valetkey-broker-login" "$here/bin/valetkey-broker-check" "$here/bin/valetkey-broker-routes" \
    "$here/bin/valetkey-broker-update" "$here/bin/valetkey-broker-approval" "$here/bin/with-bw-session" "$libexec/bin/"
  for old in relay-login relay-check relay-routes relay-update relay-approval; do  # their place and name before 0.3
    rm -f "/usr/local/sbin/${old:?}"
  done
  bash "$repo/client/install-version-tools" || return 1
  install -m 644 "$repo/VERSION" "$libexec/VERSION"
  install -m 644 "$here/CONFIG_EPOCH" "$libexec/CONFIG_EPOCH"
  # Which commit this is is only known to valetkey-broker-update, which writes COMMIT after this script.
  rm -f "$libexec/COMMIT"
  # The units call /usr/local/bin/fnox; point it at FNOX_BIN when fnox lives elsewhere.
  [ "$fnox" = /usr/local/bin/fnox ] || ln -sf "$fnox" /usr/local/bin/fnox
}
step "valetkey $(cat "$repo/VERSION") in $libexec; valetkey-broker update|routes|login|check|approval" install_code

# --- what migrate-names.sh left for the updater from 0.4 or earlier ---
# That updater, while it is the one running this script, still writes to the old paths: it finds
# them as links. Any later run removes the links and the old copies of the two keys.
old_libexec=/usr/local/libexec/relay
if [ -d "$old_libexec" ] && [ ! -L "$old_libexec" ]; then
  rm -rf "$old_libexec"
  ln -s valetkey-broker "$old_libexec"
fi
case $repo in
  /var/lib/relay/*) ;;
  *) for l in "$old_libexec" /etc/relay /var/lib/relay/update; do
       if [ -L "$l" ]; then rm -f "$l"; ok "removed the old link $l"; fi
     done
     if [ -d /var/lib/relay ] && [ ! -L /var/lib/relay ]; then rmdir /var/lib/relay 2>/dev/null || true; fi
     for k in relay-loopback relay-approval-front relay-approval-key relay-update-key; do
       # Only once its replacement exists: the two keys are copied by migrate-names.sh, the two secrets made below.
       if [ -f "$credstore/$k" ] && [ -f "$credstore/valetkey-broker-${k#relay-}" ]; then rm -f "$credstore/$k"; fi
     done ;;
esac

# --- configuration (the owner's edits are never overwritten) ---
install -d -m 755 /etc/valetkey-broker /etc/valetkey-broker/approved-routes.d
for f in routes.toml entry.toml vault.env; do
  case $f in vault.env) example=vault.example.env ;; *) example=${f%.toml}.example.toml ;; esac
  if [ -f "/etc/valetkey-broker/$f" ]; then
    ok "kept existing /etc/valetkey-broker/$f"
  else
    step "/etc/valetkey-broker/$f from config/$example (edit it for your machines)" \
      install -m 644 "$conf_src/$example" "/etc/valetkey-broker/$f"
  fi
done
# entry.toml: fill in this broker's tailnet name if it still has the example one.
dns=$(tailscale status --json | jq -r '.Self.DNSName // empty' | sed 's/\.$//')
if [ -n "$dns" ] && grep -q '^tailnet_name *= *"broker.example.ts.net"' /etc/valetkey-broker/entry.toml; then
  sed -i "s|^tailnet_name *= *\"broker.example.ts.net\"|tailnet_name = \"$dns\"|" /etc/valetkey-broker/entry.toml
  ok "/etc/valetkey-broker/entry.toml tailnet_name = $dns"
fi
step "routes and entry config valid" "$libexec/bin/valetkey-broker-routes" check

# --- loopback secret shared by the three services (32 random bytes, hex), generated once ---
for name in valetkey-broker-loopback valetkey-broker-approval-front; do
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
step "units in /etc/systemd/system" install -m 644 "$here"/systemd/valetkey-broker-*.service \
  "$here"/systemd/valetkey-broker-cert.timer /etc/systemd/system/
install -d -m 755 /etc/systemd/system/valetkey-broker-front.service.d
install -m 644 "$here/systemd/approval-front.conf" /etc/systemd/system/valetkey-broker-front.service.d/approval-front.conf
systemctl daemon-reload
if [ -f "$credstore/valetkey-broker-approval-key" ]; then
  step "restart enrolled approval worker" systemctl restart valetkey-broker-approval.service
fi
for u in valetkey-broker-local valetkey-broker-infra valetkey-broker-entry valetkey-broker-front; do
  systemctl enable "$u.service" >/dev/null 2>&1 || fail "enable $u"
done
ok "enabled valetkey-broker-local, valetkey-broker-infra, valetkey-broker-entry, valetkey-broker-front"

# --- TLS certificate (needs HTTPS Certificates on in the tailnet) + weekly renewal ---
cert_ok=yes
if "$libexec/valetkey-broker-cert" >/dev/null; then
  ok "certificate in /var/lib/valetkey-broker-front/tls"
else
  cert_ok=no
  echo "FAIL  tailscale cert (turn on HTTPS Certificates in the admin console, then re-run)" >&2
fi
step "valetkey-broker-cert.timer (weekly)" systemctl enable --now valetkey-broker-cert.timer --quiet

# --- start what has credentials ---
rc=0
"$libexec/bin/valetkey-broker-routes" install || rc=1   # valetkey-broker-local, valetkey-broker-infra: api.toml + restart
if [ -f "$credstore/bw-unlock-entry" ] && [ -f /var/lib/valetkey-broker-entry/org.json ]; then
  systemctl restart valetkey-broker-entry.service
  sleep 3
  if systemctl is-active --quiet valetkey-broker-entry.service; then ok "valetkey-broker-entry running"
  else echo "FAIL  valetkey-broker-entry not running; see: journalctl -u valetkey-broker-entry -n 30" >&2; rc=1; fi
else
  skip "valetkey-broker-entry: run valetkey-broker login entry <email> first"
fi
if [ "$cert_ok" = yes ]; then
  systemctl restart valetkey-broker-front.service
  sleep 2
  if systemctl is-active --quiet valetkey-broker-front.service; then ok "valetkey-broker-front running on https://$dns"
  else echo "FAIL  valetkey-broker-front not running; see: journalctl -u valetkey-broker-front -n 30" >&2; rc=1; fi
else
  skip "valetkey-broker-front: no certificate yet"; rc=1
fi

echo
echo "Next: edit /etc/valetkey-broker/routes.toml, entry.toml and vault.env for your machines, then"
echo "      sudo valetkey-broker login local <email>; sudo valetkey-broker login infra <email>; sudo valetkey-broker login entry <email>"
echo "      sudo valetkey-broker check"
exit $rc
