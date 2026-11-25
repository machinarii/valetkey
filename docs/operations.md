# Operate Valetkey

## Everyday use

**Is an authorized item present?** This does not test upstream authentication.

```sh
valetkey-broker check infra stripe-api-key-shop-prod pgpassword-shop-qa
# ok  stripe-api-key-shop-prod
# missing  pgpassword-shop-qa
```

**Add a key (agent or you).** Name it `<key>-<project>-<env>` (`dev|qa|prod`), or `<key>` for a tool-level key:

```sh
valetkey-broker request pgpassword-shop-qa --org infra --project shop --purpose "shop qa database"
# request r_8f2c… (pending)
# Enter it with: valetkey-entry r_8f2c…   or   https://broker.<tailnet>.ts.net/entry — your choice
```

Then enter the value on a separate approver device, whichever way you like:

- **Terminal:** `valetkey-entry` lists pending requests, and `valetkey-entry r_8f2c…` asks for the value twice at a hidden prompt. For multi-line values (`--notes`), end with a line containing only `.`.
- **Web:** open `https://broker.<tailnet>.ts.net/entry` on a device listed in `owner_nodes`. Each request is a card: type the value and press **Store secret**.

`valetkey-broker status r_8f2c…` turns to `stored`. `valetkey-entry add <name> --org … --purpose …` does the same without an agent.

**Replace a key.** `valetkey-broker request <name> --org … --replace …` or `valetkey-entry replace <name> --org …`. The old value becomes `<name>-backup-YYYYMMDD`. If that backup already exists, replacement is refused until the owner resolves the collision; no existing backup is deleted automatically.

**A key stopped working.** Agents report the item name and failing status. Only an owner or
separate trusted server may check backups, newest first; never run this in an agent session:

```sh
valetkey-broker backups infra stripe-api-key-shop-prod     # 20261106, 20261020 …
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

**Revoke a machine.** Remove it from every `nodes` list and run `sudo valetkey-broker routes install`, or drop it from the tailnet ACL.

## Configuration

All configuration files on the broker hold names only.

**`/etc/broker/routes.toml`** decides who may use what. Every entry has `org` (`local` or `infra`) and `nodes` (Tailscale machine names). See [`config/routes.example.toml`](../config/routes.example.toml).

| Table | Keys | URL |
|---|---|---|
| `[security]` | `agent_nodes`, `trusted_value_nodes` (both default to empty) | Broker-enforced roles; raw reads default to denied |
| `[api.<route>]` | `item`, `upstream`, `auth` (`"Bearer {}"`), `methods`, `paths` | `/<org>/<route>/<path>` |
| `[value.<name>]` | `item` | `/<org>/value/<name>` |
| `[project.<name>]` | `items` (exact names or `*-suffix` globs) | `/<org>/<project>/value/<item>[/notes]` |

Route names may not be `value`, `check`, `refresh`, `names`, `v1` or `backups`. After editing: `sudo valetkey-broker routes check`, then `sudo valetkey-broker routes install`.

**`/etc/broker/entry.toml`** sets `owner_nodes` (your devices, which are the only ones that may submit values) and `tailnet_name` (the broker's DNS name, filled in by the installer). After editing: `sudo valetkey-broker routes check && sudo systemctl restart broker-entry`.

**`/etc/broker/vault.env`** is read by `valetkey-broker login`:

| Key | Default |
|---|---|
| `VAULT_URL` | `https://vault.<tailnet>.ts.net` |
| `LOCAL_ORG` / `LOCAL_COLLECTION` | `local-org` / `local-broker-collection` |
| `INFRA_ORG` / `INFRA_COLLECTION` | `infra-org` / `infra-broker-collection` |

After changing org or collection names, run `valetkey-broker login` again for the affected broker services.

**Clients:**

| Setting | Where |
|---|---|
| Broker URL for `valetkey-broker` and `valetkey-entry` | `$BROKER_URL`, else `~/.config/valetkey/broker-url` (written by `install.sh client --broker-url`) |
| Broker URL for the `vault` shim | `VAULT_ADDR`, which fnox sets from the provider's `address` |

**Broker install environment** (`install.sh broker --with-deps`): `FNOX_VERSION` (default `1.35.3`), `BROKER_HOSTNAME` (default `broker`), `BROKER_TAGS` (default `tag:broker`), `FNOX_BIN`.

## Commands

### Command names

Every command you type starts with `valetkey-`. Since 0.4 each server has one name: the broker's
command is `valetkey-broker` and the bellhop's is `valetkey-bellhop`. The earlier names are gone:
each installer removes them (only valetkey's own files) when it next runs.

| Command | In 0.3 | Before 0.3 | Where |
|---|---|---|---|
| `valetkey-broker <command>` | `valetkey‑relay <command>` | `relay <command>` | every machine |
| `sudo valetkey-broker update` | `sudo valetkey‑relay update` | `sudo relay‑update install` | broker |
| `sudo valetkey-broker rollback`, `sudo valetkey-broker setup <repo>` | `sudo valetkey‑relay rollback`, `… setup <repo>` | `sudo relay‑update rollback`, `… setup <repo>` | broker |
| `sudo valetkey-broker routes check`, `sudo valetkey-broker routes install [org]` | `sudo valetkey‑relay routes …` | `sudo relay‑routes …` | broker |
| `sudo valetkey-broker login <org> <email>` | `sudo valetkey‑relay login …` | `sudo relay‑login …` | broker |
| `sudo valetkey-broker check [org]` | `sudo valetkey‑relay check …` | `sudo relay‑check …` | broker |
| `sudo valetkey-broker approval pair <device>` | `sudo valetkey‑relay approval …` | `sudo relay‑approval …` | broker |
| `valetkey-entry [<id>]` | same | `vault‑entry` | approver devices |
| `valetkey-bellhop --list`, `valetkey-bellhop --request …` | same | `bellhop …` | agent machines |
| `sudo valetkey-sweep` | same | `sudo bellhop‑sweep` | agent machines |
| `sudo valetkey-bellhop --requests`, `sudo valetkey-bellhop --done <id>` | `sudo valetkey‑desk …` | `python3 ‑I …/desk.py …` | bellhop |
| `valetkey-fnox-to-broker` | `valetkey‑fnox‑to‑relay` | `fnox‑to‑relay` | trusted servers, approver devices |
| `valetkey-agent-setup` | same | | agent machines (Homebrew) |

The installer's words changed with them: `./install.sh bellhop` (was `install.sh desk`), `--broker-url`
(was `--relay-url`), `--bellhop-url` (was `--desk-url`), `--no-bellhop` (was `--no-desk`) and the
`BROKER_URL` variable (was `RELAY_URL`).

Three names stay as they are: `vault` (fnox calls it by that name), the stand-ins that carry each
bellhop tool's own name, and the broker's own tool files, which live in `/usr/local/libexec/broker/bin`
and are run through `valetkey-broker`. On the broker, `valetkey-broker check` is the broker's own
check; on other machines it is the client's. On the bellhop, `valetkey-bellhop` is the bellhop's own
command; on agent machines it is the stand-in.

Since 0.5 the names on disk match the commands. The broker's files and services say `broker`
(`/etc/broker/routes.toml`, `broker-front`), and the bellhop's say `bellhop`
(`/etc/bellhop/bellhop.toml`, the `bellhop` user, the `/bellhop` address). A machine installed
before 0.5 is moved over by its installer:
see [Updating to 0.5](updating-valetkey.md#updating-to-05-names-on-disk).

| Command | Runs on | Does |
|---|---|---|
| `sudo ./install.sh vaultwarden` | vault host | Installs Docker and Tailscale, runs Vaultwarden and publishes it on the tailnet only. |
| `./install.sh client [--broker-url URL] [--agent\|--owner] [--prefix DIR] [--with-deps]` | client | `--agent`: metadata CLI and guidance only. Otherwise installs consumer tools; `--owner` adds private entry; `--with-deps` adds Tailscale and fnox. |
| `sudo ./install.sh update` | existing Linux installation | Refreshes just the version commands from the checkout. Preserves the commands of an existing full client. Use `--prefix BIN_DIR` for a nondefault install. |
| `valetkey-broker version` or `valetkey-broker --version` | all roles | Prints the installed Valetkey CLI version offline, for example `0.1.2`. |
| `sudo valetkey-broker approval pair OWNER_NODE` | trusted broker console | Issue a ten-minute authenticator pairing link with a locally rendered QR code. [Pair and approve a route](route-approval.md). |
| `valetkey-broker request-route --help` | agent | Propose a fixed bearer API route and print its owner approval link. |
| `valetkey-broker update` | all roles | Prints the highest stable `vMAJOR.MINOR.PATCH` tag from the public Valetkey repository on GitHub. Requires network access and only checks for updates. |
| `valetkey-broker guide` / `./install.sh guidance` | any | Prints offline safety rules for persistent agent instructions; no credential access. |
| `sudo ./install.sh broker [--with-deps]` | broker | Installs or updates the broker. Idempotent. |
| `./install.sh doctor` / `version` / `uninstall-client` | any | Status, version, removal. |
| `valetkey-broker check <org> <name>…` | client | `ok` / `missing` / `not-allowed` per name; exits 1 unless all are `ok`. |
| `valetkey-broker request <name> --org … [--project P] --purpose … [--notes] [--replace]` | client | Opens an entry request. |
| `valetkey-broker status <id>` · `valetkey-broker backups <org> <name>` | client | Request state · backup dates. |
| `vault kv get -field=<field> <path>/<name>` | client | The fnox shim. Prints one value; used by fnox, not by people. |
| `valetkey-entry [<id> \| add … \| replace …]` | approver device | Lists requests and enters values at a hidden prompt. |
| `valetkey-fnox-to-broker --project P --env E --org O` | client | Turns `fnox list` output into a broker `fnox.toml`. |
| `sudo valetkey-broker login <local\|infra\|entry> <email>` | broker | Hidden-prompt login; stores the encrypted unlock; writes `org.json`. |
| `sudo valetkey-broker check [local\|infra\|entry]` | broker | Lock state, `ok`/`FAIL` per item and route, unit state, recent audit lines. |
| `sudo valetkey-broker routes check \| install [local\|infra]` | broker | Validates the configs; regenerates fnox proxy rules and restarts the read services. |
| `sudo ./install.sh agent [--broker-url URL] [--bellhop-url URL] [--tools a,b] [--user NAME] [--no-policy] [--t3-url URL \| --no-t3]` | agent machine (Linux) | Everything an agent machine needs, as root-owned files: `valetkey-broker` in agent mode, the `bellhop` stand-in and tool links, the sweep and its timer, the MCP policy (with T3 Code's own tools when T3 is installed; see [T3 Code](t3-code.md)). No keys. |
| `./install.sh claude [--yes] [--dry-run] [--hide-env NAMES]` | agent machine, as the agent's user | Asks what to block, then merges valetkey's rules into `~/.claude/CLAUDE.md` and `~/.claude/settings.json`. Keeps what is there; backs both files up. |
| `sudo ./install.sh bellhop` | bellhop | Installs or updates the bellhop behind the same TLS front door. No tools, no logins. |
| `<tool> [args…]` · `valetkey-bellhop --list` | agent machine | Runs a listed tool on the bellhop · shows what this machine may use. Link it under a tool's name to use that name. |
| `valetkey-bellhop --request cli <name> [source] [why]` | agent machine | Asks the owner to install a tool on the bellhop. |
| `sudo valetkey-bellhop --requests` · `sudo valetkey-bellhop --done <id>` | bellhop | Lists waiting install requests · clears one. |
| `sweep.py [--accept]` | agent machine, as root | Reports logins and new tools by name · takes the current tools as known. |
| `sudo valetkey-broker setup <repo> \| install \| rollback` | broker | Fetches a new version, shows what changes, installs after `y`; puts the previous code back if the broker services do not return, or on request. |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `valetkey-broker check` → `not-allowed` | Route denied, item unmatched, or raw consumer absent from `trusted_value_nodes`. Never fix this by trusting an agent machine. |
| `valetkey-broker check` → `missing` | No item with that name in the org's collections. Check the name, or `valetkey-broker request` it. |
| `403` from the broker | The caller isn't a tailnet peer (a LAN address, or another tailnet). |
| Connection timeout | The tailnet ACL doesn't grant `tag:broker:443`, or the broker is down (`./install.sh doctor`). |
| Every item `FAIL` but the account is `unlocked` | The collection access is set to *hide passwords*. Use *Can view*. |
| A new project's items are `missing` | Either the `-infra`/`-entry` accounts weren't granted the new nested collection, or the broker services haven't seen it yet. Grant it, run `sudo valetkey-broker login infra …` and `sudo valetkey-broker login entry …` again, then `sudo valetkey-broker check infra`. |
| `no collection for project …` on entry | Create `infra-broker-collection/<project>` first, grant it, then run `valetkey-broker login entry …` again. |
| fnox runs the wrong `vault` | Put the valetkey prefix before HashiCorp Vault on `PATH`. |
| `broker-front` fails after setup | HTTPS Certificates are off in the tailnet DNS settings. Turn them on and re-run `install.sh broker`. |
| The broker stops answering during `valetkey-broker check` or `valetkey-broker routes install`, or after adding `[api.*]` routes | Out of memory: fnox fetches one vault item per API route when a read service starts, each through the Bitwarden CLI. Give the VM 2 GB and 2 cores (Proxmox: `qm stop <id>; qm set <id> --memory 2048 --cores 2; qm start <id>`). |
| A broker unit is inactive | It has no login yet (the units wait for `valetkey-broker login`). `journalctl -u broker-<name> -n 30` shows why. |
| Every route of one org answers `502 backend unavailable` | That read service restarts in a loop because fnox cannot resolve an `[api.*]` item: it is missing, or its name is contained in another item's name (a `--replace` backup). `sudo valetkey-broker check <org>` names it. Fix the item, then run `sudo valetkey-broker routes install <org>`. |
| An `[api.*]` call answers `502 upstream error: SSLCertVerificationError` | The broker predates the Python 3.13 fix (fnox's proxy certificates fail the strict default check). Update the broker. |
| git through a route answers `400` | The item holds the raw token. See [Git through the broker](git-through-the-broker.md). |
