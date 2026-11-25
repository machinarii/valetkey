#!/bin/bash
# Run by broker/setup.sh (root), before it does anything else. A broker installed before 0.5 keeps
# its files, users and services under the name "relay". This moves them to "broker", once:
#   users    relay-{local,infra,entry,front}       -> broker-...  (same ids, so files keep their owner)
#   folders  /etc/relay, /var/lib/relay, /var/lib/relay-{local,infra,entry,approval} -> .../broker...
#   keys     relay-approval-key, relay-update-key  -> broker-...  (decrypted and encrypted again in
#            one pipe, never written in between; the old files stay until the next update)
#   units    relay-*.service, relay-cert.timer     stopped and removed; setup.sh installs broker-*
# Nothing is changed until every check has passed, and a second run skips what is already done.
# The updater that starts this one (0.4 or earlier) still uses the old paths until it exits, so
# /etc/relay and /var/lib/relay stay behind as links; setup.sh removes them at the next update.
# There is no automatic way back: snapshot or back up the machine before updating.
set -euo pipefail
root=${BROKER_MIGRATE_ROOT:-}  # tests only: a directory used in place of /
[ -n "$root" ] || [ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
etc=$root/etc
var=$root/var/lib
cs=$root/etc/credstore.encrypted
units=$root/etc/systemd/system
USERS="local infra entry front"
KEYS="approval-key update-key"  # the loopback secrets are not moved: setup.sh makes new ones

ok() { echo "ok    $*"; }
is_dir() { [ -d "$1" ] && [ ! -L "$1" ]; }  # a real folder, not a link an earlier run left
has_user() { getent passwd "$1" >/dev/null; }
folders() {  # old and new path, one pair per line
  echo "$etc/relay $etc/broker"
  echo "$var/relay $var/broker"
  local n
  for n in local infra entry approval; do echo "$var/relay-$n $var/broker-$n"; done
}

todo=no
while read -r old new; do if is_dir "$old"; then todo=yes; fi; done < <(folders)
for n in $USERS; do if has_user "relay-$n"; then todo=yes; fi; done
for k in $KEYS; do if [ -f "$cs/relay-$k" ] && [ ! -f "$cs/broker-$k" ]; then todo=yes; fi; done
for f in "$units"/relay-*; do if [ -e "$f" ] || [ -L "$f" ]; then todo=yes; fi; done
[ "$todo" = yes ] || exit 0

# --- checks: nothing below this block runs unless all of them pass ---
problems=()
for c in systemctl usermod groupmod systemd-creds getent; do
  command -v "$c" >/dev/null || problems+=("$c is missing")
done
for n in $USERS; do
  if has_user "relay-$n" && has_user "broker-$n"; then problems+=("users relay-$n and broker-$n both exist"); fi
done
while read -r old new; do
  if is_dir "$old" && { [ -e "$new" ] || [ -L "$new" ]; }; then problems+=("$old and $new both exist"); fi
done < <(folders)
for k in $KEYS; do
  if [ -f "$cs/relay-$k" ] && [ ! -f "$cs/broker-$k" ] \
     && ! systemd-creds decrypt --name="relay-$k" "$cs/relay-$k" - >/dev/null 2>&1; then
    problems+=("$cs/relay-$k cannot be decrypted on this machine")
  fi
done
if [ ${#problems[@]} != 0 ]; then
  printf 'FAIL  %s\n' "${problems[@]}" >&2
  echo "FAIL  names not moved from relay to broker; nothing was changed" >&2
  exit 3  # setup.sh tells this apart from a move that stopped part-way
fi

echo "Moving this broker's names on disk from relay to broker."
echo "The updater cannot put this back. If the services do not return, restore the snapshot or"
echo "backup taken before this update."

# 1. stop everything that runs as the old users or holds the old folders open
old_units=(relay-front.service relay-entry.service relay-local.service relay-infra.service
           relay-approval.service relay-cert.timer relay-cert.service)
systemctl stop "${old_units[@]}" 2>/dev/null || true
systemctl disable "${old_units[@]}" 2>/dev/null || true
ok "old services stopped"

# 2. the two keys that cannot be made again: the authenticator pairing and the repository deploy key
for k in $KEYS; do
  if [ -f "$cs/relay-$k" ] && [ ! -f "$cs/broker-$k" ]; then
    systemd-creds decrypt --name="relay-$k" "$cs/relay-$k" - \
      | systemd-creds encrypt --name="broker-$k" - "$cs/broker-$k.new"
    chmod 600 "$cs/broker-$k.new"
    mv -f "$cs/broker-$k.new" "$cs/broker-$k"
    ok "key relay-$k is now broker-$k"
  fi
done

# 3. folders, before the users: usermod below only rewrites where the home is
while read -r old new; do
  if is_dir "$old"; then
    mv "$old" "$new"
    case $old in "$etc/relay"|"$var/relay") ln -s broker "$old" ;; esac  # for the updater still running
    ok "${old#"$root"} is now ${new#"$root"}"
  fi
done < <(folders)

# 4. users and groups keep their ids and get the new names
for n in $USERS; do
  home=/var/lib/broker-$n
  [ "$n" != front ] || home=/var/lib/broker
  if has_user "relay-$n"; then usermod -l "broker-$n" -d "$home" "relay-$n"; fi
  if getent group "relay-$n" >/dev/null; then groupmod -n "broker-$n" "relay-$n"; fi
done
ok "users relay-* are now broker-*"

# 5. old units; settings you added beside one (a .service.d folder) move to the new unit's name
for d in "$units"/relay-*.service.d; do
  [ -d "$d" ] || continue
  rm -f "$d/approval-front.conf"  # ours: setup.sh installs it again
  new=$units/broker-${d##*/relay-}
  if [ -z "$(ls -A "$d")" ]; then
    rmdir "$d"
  elif [ -e "$new" ]; then
    echo "warn  $d kept: $new already exists; move its files yourself" >&2
  else
    mv "$d" "$new"
    echo "note  your settings in ${d##*/} moved to ${new##*/}; check any path in them that says relay"
  fi
done
for f in "$units"/relay-*.service "$units"/relay-cert.timer "$units"/*.wants/relay-*; do
  if [ -e "$f" ] || [ -L "$f" ]; then rm -f "$f"; fi
done
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true
ok "old units removed"

# 6. the updater's copy of the old code no longer fits these names
rm -rf "$var/broker/update/backup"
rm -f "$var/broker/update/in-progress"
ok "names moved from relay to broker"
