# valetkey

**Your machines get a valet key, never the master.**

valetkey is a self-hosted credential broker. It pairs [Vaultwarden](https://github.com/dani-garcia/vaultwarden) with [fnox](https://fnox.jdx.dev) over [Tailscale](https://tailscale.com). Agents call broker API routes; separate trusted services can opt into raw credentials. Keeping keys unread by agents requires both broker policy and machine isolation.

**Start with [secure setup](docs/secure-setup.md).** Never run agents on the broker, vault server, owner entry device, trusted raw-value consumer or their management host. Raw reads are denied by default; existing deployments need the migration steps in that guide before upgrading.

A valet key starts the car but won't open the trunk. valetkey gives each machine the same kind of key: it can use the few credentials its routes allow, for as long as it is on your tailnet, and nothing else. The master key stays in one place: a small broker VM that is the only thing allowed to unlock the vault.

---

## Contents

- [Why](#why)
- [How it works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Getting started](#getting-started)
- [Everyday use](#everyday-use)
- [Configuration](#configuration)
- [Commands](#commands)
- [Security model](#security-model)
- [Working with AI agents](#working-with-ai-agents)
- [Upgrading](#upgrading)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Development](#development)
- [Status and license](#status-and-license)

---

## Why

Secrets tend to spread across a fleet: an `.env.local` on the dev box, a key in the laptop's Keychain, an age-encrypted file with its private key next to it, a token pasted into a VM's systemd unit. Every copy is one more place to leak from. Once AI agents run on those machines, every copy is also something an agent can read and repeat back into a chat transcript.

Password managers solve storage. They don't solve *use*: to use a key a process needs the value, so the value ends up on the machine. Secret managers built for that (Vault, Infisical, Doppler) bring tokens of their own, and a token on a client is just another secret to protect.

valetkey takes a different route:

| Problem | valetkey's answer |
|---|---|
| Keys copied onto every machine | Agent machines receive no raw provider key through approved API routes. Separate trusted consumers fetch only what their processes need. Existing secret files are not removed by Valet. |
| Broker client tokens to protect and rotate | **Identity is the machine.** The broker asks Tailscale who is calling (`tailscale whois`). There is no additional Valet bearer token; Tailscale identity still needs protecting. |
| Agents that can read and print secrets | Declared agent machines use API routes and cannot read raw values. `relay check` reports storage/access status, not upstream authentication. |
| Pasting a new key into chat | The owner types the value into a **hidden terminal prompt or a web page on the tailnet**. The agent picks the name, so naming can't drift. |
| A key that silently expired | **Replace with backup**: the old value is kept as `<name>-backup-YYYYMMDD`. Only trusted consumers/operators may use backups. |

---

## How it works

```
 laptop / devbox / bots                    broker VM                                   Vaultwarden
┌─────────────────────────┐  tailnet     ┌──────────────────────────────────────┐    ┌─────────────┐
│ fnox exec -- npm start  │  HTTPS       │ relay-front  :443 (Tailscale cert)    │    │             │
│   └ vault shim ─────────┼────────────▶ │   /local  → relay-local  127.0.0.1:8441 ──▶│ local-org   │
│ relay check / request   │              │   /infra  → relay-infra  127.0.0.1:8442 ──▶│ infra-org   │
│ curl …/local/stripe/v1  │              │   /entry  → relay-entry  127.0.0.1:8443 ──▶│ (writes)    │
└─────────────────────────┘              └──────────────────────────────────────┘    └─────────────┘
 identity = Tailscale machine name         each relay: own Linux user, own Vaultwarden
 nothing stored on the client              account, own encrypted unlock, own audit log
```

**The vault.** Vaultwarden, published only on the tailnet with `tailscale serve`. Keys are sorted into two organizations by *where they are used*:

- **`local`**: your own machines and network (home lab, personal tools, push URLs).
- **`infra`**: deployed and cloud services, with one nested collection per project (`infra-broker-collection/shop`).

**The broker.** One small Linux VM, and the only machine that unlocks a vault. It runs four systemd services, each as its own system user:

| Service | Vault access | Does |
|---|---|---|
| `relay-front` | none | TLS on the broker's Tailscale address, port 443. Routes `/local`, `/infra` and `/entry` to the loopback services and stamps each request with the caller's tailnet IP. |
| `relay-local` | reads `local-org` (view-only account) | Serves `local` values, checks and API routes. |
| `relay-infra` | reads `infra-org` (view-only account) | The same for `infra`. |
| `relay-entry` | writes both (edit account) | Creates and replaces items, only when the owner submits a value. Unlocks for one write, then locks again. |

Each unlock password is stored with `systemd-creds`, encrypted to the VM's TPM/vTPM and host key. Read relays keep a decrypted item cache in broker memory that refreshes every 5 minutes, and immediately after every write.

**Who may use what.** `/etc/relay/routes.toml` on the broker lists, per credential, which Tailscale machines (`nodes`) may use it. There are three kinds of route:

| Route | The caller gets | Use it for |
|---|---|---|
| `[api.<route>]` | The upstream response. The broker forwards the call and `fnox proxy` injects the key for that host, those methods and those paths. | Reviewed HTTPS endpoints that cannot return credentials. |
| `[project.<name>]` | Values of that project's items, read through fnox at process start. | Apps you run with `fnox exec`. |
| `[value.<name>]` | One raw value. | Things that must hold the value themselves: SSH keys, push URLs, database URLs. |

An unknown caller (not a tailnet peer) gets `403`. A tailnet machine that isn't listed gets `not-allowed`. There is no endpoint that lists the vault.

`[security].agent_nodes` declares broker-only machines. Raw routes additionally require
`[security].trusted_value_nodes` membership (empty by default). Agent/raw and agent/owner role
conflicts are rejected. The fnox pull examples below are for separate trusted consumers only.

**Three flows.**

1. **Pull.** A project's `fnox.toml` points fnox's `vault` provider at the broker. fnox runs the `vault` shim for each secret, the shim does one HTTPS GET, and the value goes into the process's environment and nowhere else. `fnox exec -- npm run dev` works as before.
2. **Inject.** A client calls `https://broker.<tailnet>.ts.net/local/stripe-shop/v1/…` with no key. The broker adds the key on the way out. The client never holds it.
3. **Entry.** An agent runs `relay request stripe-api-key-shop-prod --org infra --project shop --purpose "…"`. You enter the value with `vault-entry <id>` in your private terminal or on `https://broker.<tailnet>.ts.net/entry`, on a separate owner device. The broker writes it, verifies it and tells the agent `stored`; the response contains no value.

More detail: [docs/architecture.md](docs/architecture.md).

---

## Features

- **No agent-side provider key required.** Approved broker API routes inject keys remotely. Existing credentials and Tailscale machine state still need protection. Revoke route access in `routes.toml` or the tailnet policy.
- **Key injection.** `[api.*]` routes add credentials to outgoing HTTPS calls (exact host, methods and path prefixes) through `fnox proxy`. The calling process never has the key.
- **Works with fnox as is.** Uses fnox's own `vault` provider with a 100-line shim. Keep `fnox exec`, profiles and `if_missing = "error"`.
- **Broker-only agent policy.** Agent nodes can check and request; raw reads require a separate trusted-consumer grant. Includes offline guidance for agent memory.
- **Owner entry, two ways.** Hidden terminal prompt (`vault-entry`) or a plain, phone-friendly web page that only your devices can submit to.
- **Replace with backup.** Each replace keeps `<name>-backup-YYYYMMDD`; same-day collisions are refused. Backups require the same trusted raw-consumer policy as the original.
  fnox may find multiple items when a backup contains the original name. The owner must resolve that ambiguity privately before restarting the route; agents must not delete backups. `relay-check` and `relay-routes install` report it.
- **Upstream 401/403 detection.** An API route that starts failing marks its item *may be expired*. You see the mark next to a one-click replace.
- **Least privilege on the broker.** Separate system users, separate Vaultwarden accounts, view-only read accounts, a write account unlocked for seconds at a time, and hardened systemd units (`ProtectSystem=strict`, `NoNewPrivileges`, and more).
- **Names only, everywhere.** Audit logs, checks, errors and installer output contain item names and `ok` / `FAIL`, never values.
- **No runtime dependencies beyond the tools.** Python standard library only. The broker needs `bw`, `fnox`, `jq` and `tailscale`, and `install.sh broker --with-deps` installs them.
- **One-command installs** for all three parts: `install.sh vaultwarden`, `install.sh broker --with-deps` and `install.sh client --with-deps` bring their own Docker, Tailscale, Bitwarden CLI and fnox. Proxmox scripts can create the Vaultwarden container and the broker VM from scratch.

---

## Requirements

| Where | Needs |
|---|---|
| **Tailnet** | Tailscale with MagicDNS and HTTPS Certificates turned on. You need admin access to edit the ACL. |
| **Vaultwarden** | Any host with Docker (the Proxmox LXC script is optional). Vaultwarden 1.37.2+ for current Bitwarden clients. |
| **Broker** | Debian 12+ or Ubuntu 24.04+ with systemd ≥ 250, Python ≥ 3.11 and a TPM or vTPM (for `systemd-creds`). 2 vCPU, 2 GB RAM and 8 GB disk. Each relay runs the Bitwarden CLI, which is heavy: 1 GB runs out once an org has more than a few `[api.*]` routes. |
| **Clients** | Python ≥ 3.9 (macOS's `/usr/bin/python3` works), Tailscale, and [fnox](https://fnox.jdx.dev) for project pulls (`install.sh client --with-deps` installs both). |

---

## Getting started

Allow about an hour for the first setup, most of it creating Vaultwarden accounts. You type every password yourself, into a hidden prompt or Vaultwarden's own web page. None of them goes into this repo, a command argument or a chat.

### 1. Prepare the tailnet

In the Tailscale admin console:

1. **DNS:** turn on **MagicDNS** and **HTTPS Certificates**.
2. **Access controls:** merge [`deploy/tailscale/acl.example.hujson`](deploy/tailscale/acl.example.hujson) into your policy. Owner devices reach `tag:vault` and `tag:broker` on 443; workload machines reach only `tag:broker:443`; the broker reaches `tag:vault:443`; nothing reaches the broker over SSH. The `tests` block checks this every time you save.

### 2. Run Vaultwarden

**On Proxmox** (creates an unprivileged Debian LXC, installs Docker and Tailscale, publishes Vaultwarden on `https://vault.<tailnet>.ts.net`):

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
bash deploy/proxmox/vaultwarden-ct.sh          # CTID=210 STORAGE=local-lvm BRIDGE=vmbr0 by default
```

**On any other Debian/Ubuntu host, VM or container** (installs Docker and Tailscale, starts Vaultwarden on loopback and publishes it on the tailnet only):

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh vaultwarden                  # VAULT_HOSTNAME=vault VW_VERSION=… to override
```

Open the Tailscale login URL it prints. The scripts land in `/opt/vaultwarden`.

Optionally, to enable the admin page, run `/opt/vaultwarden/set-admin-token.sh` from an interactive console. Only the Argon2 hash is stored.

### 3. Create the accounts and organizations

In the web vault, in your browser:

1. Create **your own account** and turn on two-step login.
2. Create the three **relay accounts**, each with its own long random password from your password generator. Addresses are only login names; no mail server is needed.

   | Account | Joins | Access |
   |---|---|---|
   | `…-local` | `local-org` | *Can view* on `local-broker-collection` |
   | `…-infra` | `infra-org` | *Can view* on `infra-broker-collection` **and every nested collection** |
   | `…-entry` | both | *Can edit* on both collections **and every nested collection** |

3. As yourself, create the organizations `local-org` and `infra-org` with those collections, and invite the relay accounts. Log in once as each relay account to accept, then **Confirm** each member as yourself.
4. Close sign-ups: `pct exec 210 -- /opt/vaultwarden/close-signups.sh` (or run the script inside the container).

Vaultwarden grants access per collection: a parent does not include its children. Each new project collection (`infra-broker-collection/<project>`) needs the `-infra` and `-entry` accounts granted on it, and then `sudo relay-login infra …` and `sudo relay-login entry …` run again on the broker. The relays record collection IDs at login (`/var/lib/relay-<name>/org.json`). Different org or collection names are fine; set them in `/etc/relay/vault.env` (step 4).

### 4. Install the broker

**On Proxmox** (creates a Debian 13 VM with a vTPM, copies this repo into it and installs everything):

```sh
bash deploy/proxmox/broker-vm.sh               # VMID=211 BROKER_HOSTNAME=broker by default
```

**On any Debian/Ubuntu VM with a TPM:**

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh broker --with-deps
```

`--with-deps` installs Python, jq, Tailscale, the Bitwarden CLI and fnox (pinned version, via mise), then joins the tailnet as `broker` with `tag:broker`; open the login URL it prints. `install.sh broker` then creates the service users, installs the code to `/usr/local/libexec/relay`, writes `/etc/relay/{routes,entry}.toml` and `vault.env` from the examples, generates the loopback credential, fetches the Tailscale certificate and enables the units. It prints `ok`, `skip` or `FAIL` for each step. It is safe to re-run, and never overwrites your `/etc/relay` files.

Edit the configs for your machines (see [Configuration](#configuration)):

```sh
sudo nano /etc/relay/routes.toml   # who may use what
sudo nano /etc/relay/entry.toml    # your own devices (owner_nodes)
sudo nano /etc/relay/vault.env     # VAULT_URL and org/collection names, if not the defaults
```

### 5. Log the relays in

From a secure terminal on the broker (its console, or LAN SSH). Each command asks for that account's password at a hidden prompt:

```sh
sudo relay-login local <-local account address>
sudo relay-login infra <-infra account address>
sudo relay-login entry <-entry account address>
sudo relay-check        # expect: unlocked ×3, ok for every item and route, all units active
```

### 6. Install the clients

On each machine that uses keys:

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
./install.sh client --relay-url https://broker.<tailnet>.ts.net --with-deps
```

`--with-deps` installs what is missing: Tailscale (Linux; on macOS it points you to the download) and, except on agent machines, fnox at the version the broker is tested with (through [mise](https://mise.jdx.dev), linked into the prefix). Leave it off to install them yourself.

On agent machines, add `--agent` to install only `relay` and offline guidance. On separate owner devices, add `--owner` to also get `vault-entry`; these modes cannot be combined. Tools go to `~/.local/bin` (`--prefix` to change it). For trusted consumers, that directory must come before HashiCorp `vault` on `PATH`. `./install.sh doctor` reports installation and broker reachability.

### 7. Wire up a project

On a separate trusted consumer with no agents or agent-controlled code, add the project to
`/etc/relay/routes.toml` on the broker and reload. Merge the security lists with existing policy:

```toml
[security]
agent_nodes = ["devbox"]
trusted_value_nodes = ["app-shop"]

[project.shop]
org   = "infra"
nodes = ["app-shop"]
items = ["*-shop-qa", "*-shop-prod"]
```

```sh
sudo relay-routes install
```

Point the project's `fnox.toml` at the broker. To generate it from your current `fnox list` (names only, never values):

```sh
{ fnox list; echo "== production"; fnox list -P production --no-defaults; } \
  | fnox-to-relay --project shop --env qa --org infra > fnox.toml
```

```toml
[providers.relay]
type    = "vault"
address = "https://broker.<tailnet>.ts.net/infra"   # broker URL + org
path    = "shop"                                    # project
token   = "tailscale"                               # placeholder; identity is the machine

[secrets]
DATABASE_URL = { provider = "relay", value = "database-url-shop-qa", if_missing = "error" }
TLS_KEY      = { provider = "relay", value = "tls-key-shop-qa/notes", if_missing = "error" }
```

Check every item, then run as usual:

```sh
relay check infra $(sed -n 's/.* value = "\([^"]*\)".*/\1/p' fnox.toml)    # all ok
FNOX_DAEMON=off fnox exec -- npm run dev
```

Set `FNOX_DAEMON=off` (for example in the project's `.mise.toml` `[env]`) so values aren't cached outside the process. Once it works, delete the old `.env` files and local keys yourself, and rotate them when convenient.

### 8. Verify

From a client:

```sh
curl -fsS https://vault.<tailnet>.ts.net/alive                          # owner devices only
curl -fsS "https://broker.<tailnet>.ts.net/local/check?names=x"         # {"x": "not-allowed"} or "missing"
```

From a device that isn't on the tailnet, both should fail. On the broker, `sudo relay-check` should show `unlocked` ×3 and `ok` everywhere.

---

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

**Push with git without holding a token.** The same kind of route carries `git fetch` and `git push`: git talks to the broker, which adds the credential. [Git through the broker](docs/git-through-the-broker.md) has the form GitHub needs the token stored in, the route, and the two `git config` lines.

**A separate trusted service that must hold a value** (a push URL, an SSH key):

```sh
token=$(VAULT_ADDR=https://broker.<tailnet>.ts.net vault kv get -field=value local/kuma-push-devbox) || exit 1
```

The service, never an agent, reads its value internally. Never put it in process arguments,
logs or persistent files. This flow requires `trusted_value_nodes` and a raw route.

**Revoke a machine.** Remove it from every `nodes` list and run `sudo relay-routes install`, or drop it from the tailnet ACL.

---

## Configuration

All configuration files on the broker hold names only.

**`/etc/relay/routes.toml`** decides who may use what. Every entry has `org` (`local` or `infra`) and `nodes` (Tailscale machine names). See [`config/routes.example.toml`](config/routes.example.toml).

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

---

## Commands

| Command | Runs on | Does |
|---|---|---|
| `sudo ./install.sh vaultwarden` | vault host | Installs Docker and Tailscale, runs Vaultwarden and publishes it on the tailnet only. |
| `./install.sh client [--relay-url URL] [--agent\|--owner] [--prefix DIR] [--with-deps]` | client | `--agent`: metadata CLI and guidance only. Otherwise installs consumer tools; `--owner` adds private entry; `--with-deps` adds Tailscale and fnox. |
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
| `sudo relay-update setup <repo> \| install \| rollback` | broker | Fetches a new version, shows what changes, installs after `yes`; puts the previous code back if the relays do not return, or on request. |

---

## Security model

**Protects against:**

- a value at rest on any client machine: in files, `.env`, Keychain, shell history or logs;
- a client token leaking: there is none;
- a machine reading outside its routes, or one org's relay reading the other org;
- raw reads by a declared agent machine: broker policy refuses them, including direct HTTP requests;
- typos and naming drift when keys are added: the agent names, you type only the value;
- losing the old key on a replace: a dated backup is always kept;
- writes from non-owner machines: only untagged, non-agent `owner_nodes` may submit; stored values are verified by hash.

**Does not protect against:**

- **Code on an allowed machine.** Every process shares its Tailscale identity. If trusted consumer `app-shop` may pull `*-shop-qa`, anything on `app-shop` can. Keep routes narrow and agents on separate machines or VMs.
- **A process that already holds a value** (`[project]` and `[value]` routes). It can leak that value. `[api]` routes avoid this.
- **Root on the broker**, which can unlock everything. Run nothing else on it, manage it only from its console or LAN SSH, keep it updated and back it up.
- **An agent on an owner device.** It could submit values, capture input or browser state, or read process memory. Keep owner devices separate.
- **Upstream key disclosure or misuse.** Echo/debug and secret-export APIs can return credentials. A route also permits the upstream actions it exposes. Narrow paths, methods and provider permissions; Valet is not a general response redactor.
- **Availability.** If the broker is down, clients get no keys. Keep what you need to repair it outside valetkey.

More in [SECURITY.md](SECURITY.md).

---

## Working with AI agents

[`agents/credential-rules.md`](agents/credential-rules.md) is a drop-in section for `CLAUDE.md`, `AGENTS.md` or any agent instruction file. It teaches the agent to:

- check with `relay check` instead of reading values;
- create named requests and **always offer you both entry methods** (terminal or web);
- keep agents separate from trusted raw-key consumers and owner entry devices;
- never ask for, print or store a secret, including in Notion or other cloud documents.

---

## Upgrading

Read [the migration procedure](docs/secure-setup.md#migrate-an-existing-deployment-without-breaking-its-consumers)
first. Raw routes now need explicit `trusted_value_nodes`; no list means no raw reads. Prepare
the policy and verify replacement consumers before the change window. No deployed state is
changed by the source test suite.

```sh
git pull
sudo ./install.sh broker      # on the broker: replaces code and units, keeps /etc/relay and logins
./install.sh client           # on each client: replaces the tools, keeps the saved broker URL
```

The broker reports its installed version in `/usr/local/libexec/relay/VERSION` and in `./install.sh doctor`.

### Updating the broker in one command

After a one-time setup the broker fetches its own code, so nothing has to be copied to it:

```sh
sudo relay-update setup https://github.com/machinarii/valetkey.git   # once
sudo relay-update install                                            # each update
```

Point `setup` at this repository or at your own fork. A public repository is read over https and
needs no key. For a private one, give the `git@github.com:<owner>/<repo>.git` address instead:
`setup` then makes a read-only deploy key (the private half is encrypted with `systemd-creds`),
prints the public half for the repository's Settings → Deploy keys (leave write access off), and
prints GitHub's SSH host key fingerprint to compare with the published one.

`install` fetches the branch, shows the commit ids, the commit titles and a GitHub link to the
diff, and waits for `yes`. Nothing from the repository runs before that. It then checks
`/etc/relay` with the new parser, keeps a copy of the installed code, runs `install.sh broker`,
and waits until the relay ports that listened before listen again. If they do not, it puts the
previous code back and says so. `sudo relay-update rollback` does the same on request, for an
update that starts but misbehaves.

Run it only from the broker's console or LAN SSH, and review the diff first: anyone who can push
to the repository decides what that command installs. There is deliberately no remote or
automatic trigger.

`broker/CONFIG_EPOCH` is a number that changes whenever an update needs you to do something
first (edit `/etc/relay`, change the vault or the tailnet). `install` warns when it differs from
the installed one; the steps are listed here, newest first:

- **1** (first numbered epoch): raw routes need `[security].trusted_value_nodes`, see the
  migration procedure above.

---

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
| git through a route answers `400` | The item holds the raw token. See [Git through the broker](docs/git-through-the-broker.md). |

---

## Project layout

```
valetkey/
├── install.sh                  vaultwarden / broker / client installer, doctor
├── VERSION, Makefile
├── relay/                      broker services (Python stdlib)
│   ├── relay_front.py          TLS front door, routes by path prefix
│   ├── relay_read.py           read relay (one per org): values, checks, backups, API forwarding
│   ├── relay_entry.py          entry service: requests, owner writes, web page
│   ├── entry-page.html
│   ├── relaylib/               bw client and cache, routes, whois identity, request store
│   ├── tools/                  gen-fnox-config, vault-info, fnox-to-relay, vault-stub
│   └── tests/                  unit and policy tests with fake bw / tailscale / fnox
├── broker/
│   ├── deps.sh                 apt packages, Tailscale, bw, fnox
│   ├── setup.sh                users, code, configs, credentials, units
│   ├── bin/                    relay-login, relay-check, relay-routes, relay-update, relay-cert, with-bw-session
│   └── systemd/                relay-{front,local,infra,entry}.service, relay-cert.{service,timer}
├── client/                     relay, vault (fnox shim), vault-entry
├── config/                     routes / entry / vault.env examples
├── deploy/
│   ├── proxmox/                vaultwarden-ct.sh, broker-vm.sh
│   ├── vaultwarden/            in-container setup, admin token, close sign-ups
│   └── tailscale/              example ACL
├── agents/credential-rules.md  instruction section for AI agents
└── docs/                       secure setup, architecture, threat model, fnox vault-provider notes, git through the broker
```

---

## Development

```sh
make test     # 210+ unit and policy tests; stdlib only, fakes for bw, tailscale and fnox
make lint     # bash -n on every script, shellcheck if installed, py_compile, no real tailnet names or home paths
make dist     # dist/valetkey-<version>.tar.gz
```

The tests cover the policy edges as well as the happy path: unknown callers, callers not in `nodes`, items outside `items`, wrong org, list attempts, values never appearing in logs or error bodies, name validation, near-duplicate flags, replace-with-backup, hash verification and lock-on-every-path. CI runs them on Python 3.11 to 3.13.

---

## Status and license

Version 0.1.0. In daily use on a small fleet of Proxmox, macOS and Linux machines with AI agents; the Proxmox scripts are the most tested path.

Released under the [MIT license](LICENSE).
