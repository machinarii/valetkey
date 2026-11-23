# Install Valetkey

## Installation

Every machine installs with the same command, on Linux and on macOS. Open a terminal on that
machine and run:

```sh
curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash
```

It asks one question, which machine this is, and does the rest:

- It lists only the roles that fit this computer: all seven on Linux, three on a Mac (a Mac is
  only ever a client). On a Proxmox host it suggests "Proxmox host".
- It fills in your tailnet's broker and desk addresses from Tailscale; press Enter to accept them.
- It asks for `sudo` only for the steps that need root.
- Before it changes Claude Code or Codex settings, it says what it will change and why, and asks.
- It never asks for a key or password.

| On this machine | Choose | What happens |
|---|---|---|
| **Proxmox host** (Linux) | Proxmox host | Creates the vault container and the broker and desk VMs, and installs each one. Replaces the next three rows. |
| **Vault host** (Linux) | Vault host | Docker, Tailscale and Vaultwarden, reachable only on the tailnet. |
| **Broker** (Linux VM) | Broker | Tailscale, the Bitwarden CLI, fnox and the relay services. |
| **Desk** (Linux VM, optional) | Desk | The desk service and its front door. No tools or logins yet. |
| **Agent machine** (Linux) | Agent machine | `relay` and `bellhop` in agent mode, the hourly sweep, the Claude Code policy (asked first), then the agent rules for Claude Code and Codex. Asks for the desk address and which desk tools to link. |
| **Agent machine** (Mac) | Agent machine | `relay` in agent mode in `~/.local/bin`, no root, then the agent rules for Claude Code and Codex. |
| **Owner device** (Linux or Mac) | Owner device | `relay` and `vault-entry`, the hidden prompt for typing keys. A phone or tablet needs nothing installed: use the entry page in its browser. |
| **Trusted consumer** (Linux or Mac) | Trusted consumer | `relay`, the `vault` shim and fnox. |

Read [Containment](system-design.md#containment-where-everything-runs) first: which machine each part runs on is
what keeps the keys away from agents. Never pick two roles on one machine.

### Client tools with Homebrew (macOS and Linux)

The client tools are also Homebrew packages, one per role, on macOS and on Linux with Homebrew:

```sh
brew tap machinarii/valetkey https://github.com/machinarii/valetkey
brew trust machinarii/valetkey      # Homebrew asks you to trust a third-party tap once
brew install valetkey-agent       # agent machine: relay only
brew install valetkey-owner       # owner device: relay and vault-entry
brew install valetkey-consumer    # trusted consumer: relay and the vault shim (add fnox)
relay set-url https://broker.<tailnet>.ts.net
```

They use the `python3` already on the machine (3.9 or newer). The three packages refuse to install side by side, because one machine has one role.
`brew upgrade` keeps them current. On Linux, Homebrew may first install its own compiler and C library (a few hundred MB); the installer above needs neither. On a Linux agent machine, the installer above still does more
than the package: the desk stand-in, the sweep and the Claude Code policy. `./install.sh claude`
adds the agent rules to Claude Code and Codex in either case.

**No access to GitHub on that machine, or prefer to read the code first?** Clone the repository,
or copy it over, and run the same menu from the copy:

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey && ./install.sh
# from another computer: git archive HEAD | ssh <machine> 'mkdir -p valetkey && tar -x -C valetkey'
```

The installer fetches the code with git, or with curl when git is missing, into a temporary
folder that it deletes afterwards. `VALETKEY_REF=<tag or branch>` before `bash` installs that
version instead of `main`. The commands behind each choice are under
[One command per machine](#one-command-per-machine).

Set the machines up in this order. [Getting started](#getting-started) has every step:

1. [Prepare the tailnet](#1-prepare-the-tailnet)
2. [Run Vaultwarden](#2-run-vaultwarden) on the vault host
3. [Create the accounts and organizations](#3-create-the-accounts-and-organizations)
4. [Install the broker](#4-install-the-broker)
5. [Log the relays in](#5-log-the-relays-in)
6. [Set up the machines that use keys](#6-set-up-the-machines-that-use-keys): agent machines, owner devices, trusted consumers
7. [Wire up a project](#7-wire-up-a-project)
8. [Add the desk](#8-add-the-desk-optional) (optional)
9. [Verify](#9-verify)

Already installed? See [Upgrading](upgrading.md#upgrading).

### Requirements

| Where | Needs |
|---|---|
| **Tailnet** | Tailscale with MagicDNS and HTTPS Certificates turned on. You need admin access to edit the ACL. |
| **Vaultwarden** | Any host with Docker (the Proxmox LXC script is optional). Vaultwarden 1.37.2+ for current Bitwarden clients. |
| **Broker** | Debian 12+ or Ubuntu 24.04+ with systemd ≥ 250, Python ≥ 3.11 and a TPM or vTPM (for `systemd-creds`). 2 vCPU, 2 GB RAM and 8 GB disk. Each relay runs the Bitwarden CLI, which is heavy: 1 GB runs out once an org has more than a few `[api.*]` routes. |
| **Desk** (optional) | Debian 12+ or Ubuntu 24.04+ with systemd and Python ≥ 3.11, on its own VM. 2 vCPU, 2 GB RAM and 16 GB disk is enough for a few tools. No TPM needed. |
| **Clients** | Python ≥ 3.9 (macOS's `/usr/bin/python3` works), Tailscale, and [fnox](https://fnox.jdx.dev) for project pulls (`install.sh client --with-deps` installs both). |

### One command per machine

Each machine gets exactly one installer. Run it on that machine, from a copy of this repository. `./install.sh` with no arguments asks which machine this is and runs the right row for you.

| Machine | Command | What it installs |
|---|---|---|
| **Hypervisor** (Proxmox) | `sudo ./install.sh proxmox [--no-desk]`<br>(runs `deploy/proxmox/vaultwarden-ct.sh`, `deploy/proxmox/broker-vm.sh` and `bellhop/proxmox-desk-vm.sh`) | Creates the vault container and the broker and desk VMs, and runs the three installers below inside them |
| **Vault host** | `sudo ./install.sh vaultwarden` | Docker, Tailscale, Vaultwarden published on the tailnet only |
| **Broker** | `sudo ./install.sh broker --with-deps` | Tailscale, Bitwarden CLI, fnox, the relay services and broker tools |
| **Desk** (optional) | `sudo ./install.sh desk` | Tailscale, the desk service, the TLS front door. No tools, no logins |
| **Agent machine** | `sudo ./install.sh agent --relay-url … --desk-url … --tools …`<br>`./install.sh claude` | `relay` in agent mode, the `bellhop` stand-in, the sweep and its timer, a policy that limits MCP servers to the broker and desk (it says why and asks first). Then, as the agent's user, the rules in `CLAUDE.md` (and Codex's `AGENTS.md`, if Codex is installed) and the blocks you choose in Claude Code's settings. No keys |
| **Owner device** | `./install.sh client --owner --relay-url …` | `relay` and `vault-entry` |
| **Trusted consumer** | `./install.sh client --relay-url … --with-deps` | `relay`, the `vault` shim, fnox |

Never combine two rows on one machine, with one exception: the hypervisor hosts the vault, broker and desk as separate guests. If you use Proxmox, the first row replaces the next three.

What no installer can do for you, because each step is a decision or a secret:

- edit the tailnet policy in the Tailscale admin console;
- create the Vaultwarden accounts and organizations, and type their passwords;
- sign the relays in (`relay-login`) and sign tools in on the desk;
- write the routes and the desk's tool list: who may use what;
- take `sudo` away from the agent's user.

## Getting started

Allow about an hour for the first setup, most of it creating Vaultwarden accounts. You type every password yourself, into a hidden prompt or Vaultwarden's own web page. None of them goes into this repo, a command argument or a chat.

The table under [One command per machine](#one-command-per-machine) is the map: each step below runs on a different machine.

### 1. Prepare the tailnet

In the Tailscale admin console ([Tailscale: what valetkey relies on](system-design.md#tailscale-what-valetkey-relies-on) has the documentation links):

1. **DNS:** turn on **MagicDNS** and **HTTPS Certificates**.
2. **Access controls:** merge [`deploy/tailscale/acl.example.hujson`](../deploy/tailscale/acl.example.hujson) into your policy. Owner devices reach `tag:vault` and `tag:broker` on 443; workload machines reach only `tag:broker:443` (and `tag:desk:443` if you use the desk); the broker reaches `tag:vault:443`; nothing reaches the broker or desk over SSH. The `tests` block checks this every time you save.

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

Edit the configs for your machines (see [Configuration](operations.md#configuration)):

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

### 6. Set up the machines that use keys

**On each agent machine**, one command as root:

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh agent --relay-url https://broker.<tailnet>.ts.net
```

It installs `relay` in agent mode, the hourly sweep and a policy that limits MCP servers to the broker, all as root-owned files, and prints what is left for you. With a desk, add `--desk-url https://desk.<tailnet>.ts.net/desk --tools tool1,tool2`; each tool name becomes a stand-in. `--user NAME` says which user the agent runs as, and `--no-policy` skips the MCP policy.

Then, as the user the agent runs as, set up Claude Code itself:

```sh
./install.sh claude
```

It asks six questions about what to block, shows the changes, and writes them only when you agree. See [Working with AI agents](agent-setup.md#working-with-ai-agents).

**On each owner device and trusted consumer:**

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
./install.sh client --relay-url https://broker.<tailnet>.ts.net --with-deps
```

`--with-deps` installs what is missing: Tailscale (Linux; on macOS it points you to the download) and, except on agent machines, fnox at the version the broker is tested with (through [mise](https://mise.jdx.dev), linked into the prefix). Leave it off to install them yourself.

On macOS agent machines, where `install.sh agent` does not run, use `client --agent` to install only `relay` and offline guidance. On separate owner devices, add `--owner` to also get `vault-entry`; these modes cannot be combined. Tools go to `~/.local/bin` (`--prefix` to change it). For trusted consumers, that directory must come before HashiCorp `vault` on `PATH`. `./install.sh doctor` reports installation and broker reachability.

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

### 8. Add the desk (optional)

Only if agents need a command-line tool that keeps its own login. An HTTP API or an MCP server with a fixed key needs no desk: give it an `[api.*]` route on the broker.

**On Proxmox** (creates a Debian 13 VM and installs the desk in it):

```sh
bash bellhop/proxmox-desk-vm.sh                # VMID=212 DESK_HOSTNAME=desk by default
```

**On any Debian/Ubuntu VM that runs nothing else:**

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh desk
```

Add `tag:desk` to the tailnet policy first; the installer joins the tailnet with that tag and prints a login URL. It installs no tool and no login. [bellhop/README.md](../bellhop/README.md) covers listing tools, signing them in and the stand-in command for agent machines.

### 9. Verify

From a client:

```sh
curl -fsS https://vault.<tailnet>.ts.net/alive                          # owner devices only
curl -fsS "https://broker.<tailnet>.ts.net/local/check?names=x"         # {"x": "not-allowed"} or "missing"
```

From a device that isn't on the tailnet, both should fail. On the broker, `sudo relay-check` should show `unlocked` ×3 and `ok` everywhere.
