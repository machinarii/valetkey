#!/bin/bash
# Run inside the Vaultwarden container as root, in an interactive console (`pct enter <CTID>`).
# Prompts for the /admin password with hidden input, stores only its Argon2id hash
# in /opt/vaultwarden/secrets.env (mode 600) and restarts Vaultwarden.
set -euo pipefail
cd /opt/vaultwarden

[ -t 0 ] || { echo "Run this from an interactive console." >&2; exit 1; }
read -rsp "New admin password (16+ chars): " pw; echo
read -rsp "Repeat: " pw2; echo
[ "$pw" = "$pw2" ] || { echo "Passwords differ." >&2; exit 1; }
[ ${#pw} -ge 16 ] || { echo "Too short." >&2; exit 1; }

hash=$(printf '%s' "$pw" | argon2 "$(openssl rand -base64 32)" -e -id -k 65540 -t 3 -p 4)
unset pw pw2
umask 077
printf "ADMIN_TOKEN='%s'\n" "$hash" > secrets.env
unset hash

docker compose up -d --force-recreate >/dev/null
echo "Admin token stored (hash only). Admin page: <vault URL>/admin"
