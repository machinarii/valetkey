#!/bin/bash
# Run by broker/setup.sh (root), before it does anything else. A broker installed before 0.5 keeps
# its files, users and services under the name "relay". This moves them to "valetkey-broker", once:
#   users    relay-{local,infra,entry,front}   -> valetkey-broker-...  (same ids: files keep their owner)
#   folders  /etc/relay                        -> /etc/valetkey-broker
#            /var/lib/relay-{local,infra,entry,approval} -> /var/lib/valetkey-broker-...
#            /var/lib/relay/tls                -> /var/lib/valetkey-broker-front/tls
#            /var/lib/relay/update             -> /var/lib/valetkey-broker-update
#   keys     relay-approval-key, relay-update-key -> valetkey-broker-...  (decrypted and encrypted
#            again in one pipe, never written in between; the old files stay until the next update)
#   units    relay-*.service, relay-cert.timer    stopped and removed; setup.sh installs valetkey-broker-*
# Every new name starts with "valetkey-broker". A machine may have a user, folder, script or unit
# of its own called "broker" (a monitoring heartbeat, say): none of that is read or changed.
# Nothing is changed until every check has passed, and a second run skips what is already done.
# The updater that starts this one (0.4 or earlier) still uses the old paths until it exits, so
# /etc/relay and /var/lib/relay/update stay behind as links; setup.sh removes them at the next update.
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
  echo "$etc/relay $etc/valetkey-broker"
  echo "$var/relay/tls $var/valetkey-broker-front/tls"
  echo "$var/relay/update $var/valetkey-broker-update"
  local n
  for n in local infra entry approval; do echo "$var/relay-$n $var/valetkey-broker-$n"; done
}

# 0.5.0 named everything plain "broker" and was withdrawn the same day. Its layout is not moved from here.
if [ -f "$etc/broker/routes.toml" ]; then
  echo "FAIL  this broker was installed with 0.5.0 (/etc/broker, broker-* services), which was withdrawn." >&2
  echo "FAIL  Restore the snapshot or backup from before 0.5.0, then update again; nothing was changed" >&2
  exit 3
fi

todo=no
while read -r old new; do if is_dir "$old"; then todo=yes; fi; done < <(folders)
for n in $USERS; do if has_user "relay-$n"; then todo=yes; fi; done
for k in $KEYS; do if [ -f "$cs/relay-$k" ] && [ ! -f "$cs/valetkey-broker-$k" ]; then todo=yes; fi; done
for f in "$units"/relay-*; do if [ -e "$f" ] || [ -L "$f" ]; then todo=yes; fi; done
[ "$todo" = yes ] || exit 0

# --- checks: nothing below this block runs unless all of them pass ---
problems=()
for c in systemctl usermod groupmod systemd-creds getent; do
  command -v "$c" >/dev/null || problems+=("$c is missing")
done
for n in $USERS; do
  if has_user "relay-$n" && has_user "valetkey-broker-$n"; then problems+=("users relay-$n and valetkey-broker-$n both exist"); fi
done
while read -r old new; do
  if is_dir "$old" && { [ -e "$new" ] || [ -L "$new" ]; }; then problems+=("$old and $new both exist"); fi
done < <(folders)
for k in $KEYS; do
  if [ -f "$cs/relay-$k" ] && [ ! -f "$cs/valetkey-broker-$k" ] \
     && ! systemd-creds decrypt --name="relay-$k" "$cs/relay-$k" - >/dev/null 2>&1; then
    problems+=("$cs/relay-$k cannot be decrypted on this machine")
  fi
done
if [ ${#problems[@]} != 0 ]; then
  printf 'FAIL  %s\n' "${problems[@]}" >&2
  echo "FAIL  names not moved from relay to valetkey-broker; nothing was changed" >&2
  exit 3  # setup.sh tells this apart from a move that stopped part-way
fi

echo "Moving this broker's names on disk from relay to valetkey-broker."
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
  if [ -f "$cs/relay-$k" ] && [ ! -f "$cs/valetkey-broker-$k" ]; then
    systemd-creds decrypt --name="relay-$k" "$cs/relay-$k" - \
      | systemd-creds encrypt --name="valetkey-broker-$k" - "$cs/valetkey-broker-$k.new"
    chmod 600 "$cs/valetkey-broker-$k.new"
    mv -f "$cs/valetkey-broker-$k.new" "$cs/valetkey-broker-$k"
    ok "key relay-$k is now valetkey-broker-$k"
  fi
done

# 3. folders, before the users: usermod below only rewrites where the home is
while read -r old new; do
  if is_dir "$old"; then
    mkdir -p "$(dirname "$new")"
    mv "$old" "$new"
    case $old in  # for the updater still running, which works from the old paths
      "$etc/relay") ln -s valetkey-broker "$old" ;;
      "$var/relay/update") ln -s ../valetkey-broker-update "$old" ;;
    esac
    ok "${old#"$root"} is now ${new#"$root"}"
  fi
done < <(folders)

# 4. users and groups keep their ids and get the new names
for n in $USERS; do
  home=/var/lib/valetkey-broker-$n
  if has_user "relay-$n"; then usermod -l "valetkey-broker-$n" -d "$home" "relay-$n"; fi
  if getent group "relay-$n" >/dev/null; then groupmod -n "valetkey-broker-$n" "relay-$n"; fi
done
ok "users relay-* are now valetkey-broker-*"

# 5. old units; settings you added beside one (a .service.d folder) move to the new unit's name
for d in "$units"/relay-*.service.d; do
  [ -d "$d" ] || continue
  rm -f "$d/approval-front.conf"  # ours: setup.sh installs it again
  new=$units/valetkey-broker-${d##*/relay-}
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
rm -rf "$var/valetkey-broker-update/backup"
rm -f "$var/valetkey-broker-update/in-progress"
ok "names moved from relay to valetkey-broker"
