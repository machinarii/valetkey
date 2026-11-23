# Operate Valetkey

## Everyday use

**Is an authorized item present?** This does not test upstream authentication.

```sh
relay check infra stripe-api-key-shop-prod pgpassword-shop-qa
# ok  stripe-api-key-shop-prod
# missing  pgpassword-shop-qa
```

**Add a key (agent or you).** Name it `<key>-<project>-<env>` (`dev|qa|prod`), or `<key>` for a tool-level key:

```sh
relay request pgpassword-shop-qa --org infra --project shop --purpose "shop qa database"
# request r_8f2c… (pending)
# Enter it with: vault-entry r_8f2c…   or   https://broker.<tailnet>.ts.net/entry — your choice
```

Then enter the value on a separate owner device, whichever way you like:

- **Terminal:** `vault-entry` lists pending requests, and `vault-entry r_8f2c…` asks for the value twice at a hidden prompt. For multi-line values (`--notes`), end with a line containing only `.`.
- **Web:** open `https://broker.<tailnet>.ts.net/entry` on a device listed in `owner_nodes`. Each request is a card: type the value and press **Store value**.

`relay status r_8f2c…` turns to `stored`. `vault-entry add <name> --org … --purpose …` does the same without an agent.

**Replace a key.** `relay request <name> --org … --replace …` or `vault-entry replace <name> --org …`. The old value becomes `<name>-backup-YYYYMMDD`. If that backup already exists, replacement is refused until the owner resolves the collision; no existing backup is deleted automatically.

**A key stopped working.** Agents report the item name and failing status. Only an owner or
separate trusted consumer may check backups, newest first; never run this in an agent session:

```sh
relay backups infra stripe-api-key-shop-prod     # 20261106, 20261020 …
vault kv get -field=value shop/stripe-api-key-shop-prod-backup-20261106   # only for a consumer, never in an agent session
```

If none work, open a `--replace` request.

**Call an API without holding its key.** With this route in `routes.toml`:

```toml
[api.stripe-shop]
org      = "local"
item     = "stripe-api-key-shop-live"
upstream = "https://api.stripe.com"
auth     = "Bearer {}"
methods  = ["GET"]
paths    = ["/v1/balance"]
nodes    = ["devbox"]
```

the agent calls the broker with no key:

```sh
curl -s https://broker.<tailnet>.ts.net/local/stripe-shop/v1/balance
```

**Push with git without holding a token.** The same kind of route carries `git fetch` and `git push`: git talks to the broker, which adds the credential. [Git through the broker](git-through-the-broker.md) has the form GitHub needs the token stored in, the route, and the two `git config` lines. [Several keys for one service](several-keys-per-service.md) explains why pushing, reading the API and changing CI each get their own token.

**Run a service that needs a raw value.** Place it on a separate trusted machine
listed in `trusted_value_nodes`. Grant that machine only the item it consumes.
The service reads the value internally and keeps it out of arguments, logs and
persistent files.

**Revoke a machine.** Remove it from every `nodes` list and run `sudo relay-routes install`, or drop it from the tailnet ACL.

## Configuration

All configuration files on the broker hold names only.

**`/etc/relay/routes.toml`** decides who may use what. Every entry has `org` (`local` or `infra`) and `nodes` (Tailscale machine names). See [`config/routes.example.toml`](../config/routes.example.toml).

| Table | Keys | URL |
|---|---|---|
| `[security]` | `agent_nodes`, `trusted_value_nodes` (both default to empty) | Broker-enforced roles; raw reads default to denied |
| `[api.<route>]` | `item`, `upstream`, `auth` (`"Bearer {}"`), `methods`, `paths` | `/<org>/<route>/<path>` |
| `[value.<name>]` | `item` | `/<org>/value/<name>` |
| `[project.<name>]` | `items` (exact names or `*-suffix` globs) | `/<org>/<project>/value/<item>[/notes]` |

Route names may not be `value`, `check`, `refresh`, `names`, `v1` or `backups`. After editing: `sudo relay-routes check`, then `sudo relay-routes install`.

**`/etc/relay/entry.toml`** sets `owner_nodes` (your devices, which are the only ones that may submit values) and `tailnet_name` (the broker's DNS name, filled in by the installer). After editing: `sudo relay-routes check && sudo systemctl restart relay-entry`.

**`/etc/relay/vault.env`** is read by `relay-login`:

| Key | Default |
|---|---|
| `VAULT_URL` | `https://vault.<tailnet>.ts.net` |
| `LOCAL_ORG` / `LOCAL_COLLECTION` | `local-org` / `local-broker-collection` |
| `INFRA_ORG` / `INFRA_COLLECTION` | `infra-org` / `infra-broker-collection` |

After changing org or collection names, run `relay-login` again for the affected relays.

**Clients:**

| Setting | Where |
|---|---|
| Broker URL for `relay` and `vault-entry` | `$RELAY_URL`, else `~/.config/valetkey/relay-url` (written by `install.sh client --relay-url`) |
| Broker URL for the `vault` shim | `VAULT_ADDR`, which fnox sets from the provider's `address` |

**Broker install environment** (`install.sh broker --with-deps`): `FNOX_VERSION` (default `1.35.3`), `BROKER_HOSTNAME` (default `broker`), `BROKER_TAGS` (default `tag:broker`), `FNOX_BIN`.

## Commands

| Command | Runs on | Does |
|---|---|---|
| `sudo ./install.sh vaultwarden` | vault host | Installs Docker and Tailscale, runs Vaultwarden and publishes it on the tailnet only. |
| `./install.sh client [--relay-url URL] [--agent\|--owner] [--prefix DIR] [--with-deps]` | client | `--agent`: metadata CLI and guidance only. Otherwise installs consumer tools; `--owner` adds private entry; `--with-deps` adds Tailscale and fnox. |
| `sudo ./install.sh version-tools` | existing Linux installation | Refreshes just the version commands from the checkout. Preserves the commands of an existing full client. Use `--prefix BIN_DIR` for a nondefault install. |
| `relay version` or `relay --version` | all roles | Prints the installed Valetkey CLI version offline, for example `0.1.2`. |
| `sudo relay-approval pair OWNER_NODE` | trusted broker console | Issue a ten-minute authenticator pairing link with a locally rendered QR code. [Pair and approve a route](route-approval.md). |
| `relay request-route --help` | agent | Propose a fixed bearer API route and print its owner approval link. |
| `relay latest-version` | all roles | Prints the highest stable `vMAJOR.MINOR.PATCH` tag from the public Valetkey repository on GitHub. Requires network access and only checks for updates. |
| `relay guide` / `./install.sh guidance` | any | Prints offline safety rules for persistent agent instructions; no credential access. |
| `sudo ./install.sh broker [--with-deps]` | broker | Installs or updates the broker. Idempotent. |
| `./install.sh doctor` / `version` / `uninstall-client` | any | Status, version, removal. |
| `relay check <org> <name>…` | client | `ok` / `missing` / `not-allowed` per name; exits 1 unless all are `ok`. |
| `relay request <name> --org … [--project P] --purpose … [--notes] [--replace]` | client | Opens an entry request. |
| `relay status <id>` · `relay backups <org> <name>` | client | Request state · backup dates. |
| `vault kv get -field=<field> <path>/<name>` | client | The fnox shim. Prints one value; used by fnox, not by people. |
| `vault-entry [<id> \| add … \| replace …]` | owner device | Lists requests and enters values at a hidden prompt. |
| `fnox-to-relay --project P --env E --org O` | client | Turns `fnox list` output into a relay `fnox.toml`. |
| `sudo relay-login <local\|infra\|entry> <email>` | broker | Hidden-prompt login; stores the encrypted unlock; writes `org.json`. |
| `sudo relay-check [local\|infra\|entry]` | broker | Lock state, `ok`/`FAIL` per item and route, unit state, recent audit lines. |
| `sudo relay-routes check \| install [local\|infra]` | broker | Validates the configs; regenerates fnox proxy rules and restarts the read relays. |
| `sudo ./install.sh agent [--relay-url URL] [--desk-url URL] [--tools a,b] [--user NAME] [--no-policy]` | agent machine (Linux) | Everything an agent machine needs, as root-owned files: `relay` in agent mode, the `bellhop` stand-in and tool links, the sweep and its timer, the MCP policy. No keys. |
| `./install.sh claude [--yes] [--dry-run] [--hide-env NAMES]` | agent machine, as the agent's user | Asks what to block, then merges valetkey's rules into `~/.claude/CLAUDE.md` and `~/.claude/settings.json`. Keeps what is there; backs both files up. |
| `sudo ./install.sh desk` | desk | Installs or updates the bellhop desk behind the same TLS front door. No tools, no logins. |
| `bellhop <tool> [args…]` · `bellhop --list` | agent machine | Runs a listed tool on the desk · shows what this machine may use. Link it under a tool's name to use that name. |
| `bellhop --request cli <name> [source] [why]` | agent machine | Asks the owner to install a tool on the desk. |
| `desk.py --requests` · `desk.py --done <id>` | desk | Lists waiting install requests · clears one. |
| `sweep.py [--accept]` | agent machine, as root | Reports logins and new tools by name · takes the current tools as known. |
| `sudo relay-update setup <repo> \| install \| rollback` | broker | Fetches a new version, shows what changes, installs after `yes`; puts the previous code back if the relays do not return, or on request. |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `relay check` → `not-allowed` | Route denied, item unmatched, or raw consumer absent from `trusted_value_nodes`. Never fix this by trusting an agent machine. |
| `relay check` → `missing` | No item with that name in the org's collections. Check the name, or `relay request` it. |
| `403` from the broker | The caller isn't a tailnet peer (a LAN address, or another tailnet). |
| Connection timeout | The tailnet ACL doesn't grant `tag:broker:443`, or the broker is down (`./install.sh doctor`). |
| Every item `FAIL` but the account is `unlocked` | The collection access is set to *hide passwords*. Use *Can view*. |
| A new project's items are `missing` | Either the `-infra`/`-entry` accounts weren't granted the new nested collection, or the relays haven't seen it yet. Grant it, run `sudo relay-login infra …` and `sudo relay-login entry …` again, then `sudo relay-check infra`. |
| `no collection for project …` on entry | Create `infra-broker-collection/<project>` first, grant it, then run `relay-login entry …` again. |
| fnox runs the wrong `vault` | Put the valetkey prefix before HashiCorp Vault on `PATH`. |
| `relay-front` fails after setup | HTTPS Certificates are off in the tailnet DNS settings. Turn them on and re-run `install.sh broker`. |
| The broker stops answering during `relay-check` or `relay-routes install`, or after adding `[api.*]` routes | Out of memory: fnox fetches one vault item per API route when a read relay starts, each through the Bitwarden CLI. Give the VM 2 GB and 2 cores (Proxmox: `qm stop <id>; qm set <id> --memory 2048 --cores 2; qm start <id>`). |
| A relay unit is inactive | It has no login yet (the units wait for `relay-login`). `journalctl -u relay-<name> -n 30` shows why. |
| Every route of one org answers `502 backend unavailable` | That read relay restarts in a loop because fnox cannot resolve an `[api.*]` item: it is missing, or its name is contained in another item's name (a `--replace` backup). `sudo relay-check <org>` names it. Fix the item, then run `sudo relay-routes install <org>`. |
| An `[api.*]` call answers `502 upstream error: SSLCertVerificationError` | The broker's relay predates the Python 3.13 fix (fnox's proxy certificates fail the strict default check). Upgrade the broker. |
| git through a route answers `400` | The item holds the raw token. See [Git through the broker](git-through-the-broker.md). |
