# Install valetkey

Install valetkey with the package manager of each machine's system:

| System | Install with | Roles it covers |
|---|---|---|
| **macOS** | [Homebrew](#macos-homebrew) | agent machine, approver device, trusted server (optional) |
| **Linux** (Debian, Ubuntu) | [apt](#linux-apt) | every role, including the three servers: vault, broker and bellhop |

A phone or tablet where you type keys needs nothing installed: open the broker's entry page in its
browser. For a Proxmox VE host or another Linux distribution, see
[Other ways to install](#other-ways-to-install).

Put each role on its own machine; [Containment](system-design.md#containment-where-everything-runs)
explains why. The three servers (the vault, the broker and, optionally, the bellhop) run on Linux.
Every machine needs Tailscale; see [Requirements](#requirements).

A **trusted server** is optional. You need one only if an application must hold a key itself, such as a web app that reads its database password. Skip the role if all your tools use brokered API routes or the bellhop.

## macOS: Homebrew

Add valetkey's Homebrew tap once on each Mac:

```sh
brew tap machinarii/valetkey https://github.com/machinarii/valetkey
brew trust machinarii/valetkey
```

Then install the package for that machine's role, and point it at your broker:

| This machine | Install | Then |
|---|---|---|
| **Runs AI agents** (Claude Code, Codex, …) | `brew install valetkey-agent` | `valetkey-broker set-url https://broker.<tailnet>.ts.net`, then `valetkey-agent-setup`: valetkey's rules and settings for Claude Code and Codex, asking before each change |
| **Where you type keys** and approve routes | `brew install valetkey-approver` | `valetkey-broker set-url https://broker.<tailnet>.ts.net` |
| **A server whose app reads a key itself** (optional) | `brew install valetkey-trusted-server` | `valetkey-broker set-url https://broker.<tailnet>.ts.net`, and install [fnox](https://fnox.jdx.dev) |

- One machine, one role: the packages refuse to install side by side.
- `brew upgrade` keeps them current.
- Homebrew on Linux installs the same three packages, but apt is the usual way there and covers
  more: see below.

## Linux: apt

On Debian or Ubuntu, add valetkey's signed package repository once on each machine, install the
package and run its setup command:

```sh
curl -fsSL https://machinarii.github.io/valetkey/apt/valetkey.gpg | sudo tee /usr/share/keyrings/valetkey.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/valetkey.gpg] https://machinarii.github.io/valetkey/apt ./" \
  | sudo tee /etc/apt/sources.list.d/valetkey.list
sudo apt-get update && sudo apt-get install valetkey
valetkey-setup                     # asks which machine this is, then sets it up
```

- One package covers every role on Linux: agent machine, approver device, trusted server, broker,
  bellhop and vault host. `valetkey-setup` asks which one this machine is.
- apt checks the package's signature on every install and upgrade.
- The package installs one command, `valetkey-setup`, and the installer's files in
  `/usr/share/valetkey`.
- Installing or upgrading the package changes nothing on its own. After `sudo apt-get upgrade`,
  run `valetkey-setup` again to apply the new version to the machine's role. On the broker,
  `sudo valetkey-broker update` remains the way to update.
- On an agent machine, the setup adds more than the Homebrew package does: the hourly sweep, the
  bellhop's tool stand-ins and a root-owned Claude Code policy
  ([Installer reference](#installer-reference)).
- The setup asks for `sudo` only when it needs it, fills in your tailnet's addresses, and never
  asks for a key or password.

## Other ways to install

**A Proxmox VE host.** One command creates the vault, broker and bellhop as separate guests and
installs each one:

```sh
git clone https://github.com/machinarii/valetkey.git && cd valetkey
sudo ./install.sh proxmox          # add --no-bellhop to skip the bellhop
```

**Another Linux distribution, or straight from the source.** The apt package runs this same
installer. Run it on each machine and choose its role:

```sh
curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash
```

To read the code first, clone the repository and run `./install.sh`.

### Fresh installation on Proxmox VE

`sudo ./install.sh proxmox` checks guest IDs across the cluster before creating the vault container
(210), broker VM (211) and bellhop VM (212), then runs each guest's installer. To choose other IDs, run
for example `sudo env CTID=310 BROKER_VMID=311 BELLHOP_VMID=312 ./install.sh proxmox`. Authorize each
guest to join your tailnet when asked. If one guest fails, the others stay in place; fix that guest
before continuing.

## Then set it up

Installing puts the programs in place; the setup is where you decide who may use what. Follow
[Getting started](#getting-started), in this order:

1. [Prepare the tailnet](#1-prepare-the-tailnet)
2. [Run Vaultwarden](#2-run-vaultwarden) on the vault host
3. [Create the accounts and organizations](#3-create-the-accounts-and-organizations)
4. [Install the broker](#4-install-the-broker)
5. [Log the broker services in](#5-log-the-broker-services-in)
6. [Set up the machines that use keys](#6-set-up-the-machines-that-use-keys)
7. [Wire up a project](#7-wire-up-a-project)
8. [Add the bellhop](#8-add-the-bellhop-optional) (optional)
9. [Verify](#9-verify)

Already installed? See [Updating valetkey](updating-valetkey.md).

## Requirements

| Where | Needs |
|---|---|
| **Tailnet** | Tailscale with MagicDNS and HTTPS Certificates turned on. You need admin access to edit the ACL. |
| **Vaultwarden** | Any host with Docker (the Proxmox LXC script is optional). Vaultwarden 1.37.2+ for current Bitwarden clients. |
| **broker** | Debian 12+ or Ubuntu 24.04+ with systemd ≥ 250, Python ≥ 3.11 and a TPM or vTPM (for `systemd-creds`). 2 vCPU, 2 GB RAM and 8 GB disk. Each broker service runs the Bitwarden CLI, which is heavy: 1 GB runs out once an org has more than a few `[api.*]` routes. |
| **bellhop** (optional) | Debian 12+ or Ubuntu 24.04+ with systemd and Python ≥ 3.11, on its own VM. 2 vCPU, 2 GB RAM and 16 GB disk is enough for a few tools. No TPM needed. |
| **Clients** | Python 3.9+ on `PATH` and Tailscale access to the broker for broker commands. Trusted servers that pull project secrets also need fnox. |

On Linux, `./install.sh client --with-deps` can install Tailscale and, for a trusted server, fnox. On macOS, install Tailscale separately and join the tailnet.

The broker installer checks for the Bitwarden CLI (`bw`), fnox, Tailscale, `jq`, `systemd-creds` and `systemd-run`. On supported Debian and Ubuntu systems, `sudo ./install.sh broker --with-deps` installs the broker dependencies, including `qrencode` for authenticator pairing. See [version-command requirements](updating-valetkey.md) and [model launcher requirements](model-api-accounts.md#requirements) for those workflows.

## Installer reference

What the installer runs for each role, if you'd rather name the role yourself. Run it on that machine, from a copy of this repository; `./install.sh` with no arguments asks instead.

| Machine | Command | What it installs |
|---|---|---|
| **Hypervisor** (Proxmox) | `sudo ./install.sh proxmox [--no-bellhop]`<br>(runs `deploy/proxmox/vaultwarden-ct.sh`, `deploy/proxmox/broker-vm.sh` and `bellhop/proxmox-bellhop-vm.sh`) | Creates the vault container and the broker and bellhop VMs, and runs the three installers below inside them |
| **vault host** | `sudo ./install.sh vaultwarden` | Docker, Tailscale, Vaultwarden published on the tailnet only |
| **broker** | `sudo ./install.sh broker --with-deps` | Tailscale, Bitwarden CLI, fnox, the broker services and broker tools |
| **bellhop** (optional) | `sudo ./install.sh bellhop` | Tailscale, the bellhop service, the TLS front door. No tools, no logins |
| **Agent machine** | `sudo ./install.sh agent --broker-url … --bellhop-url … --tools …`<br>`./install.sh claude` | `valetkey-broker` in agent mode, the `bellhop` stand-in, the sweep and its timer, a policy that limits MCP servers to the broker and bellhop (it says why and asks first). Then, as the agent's user, the rules in `CLAUDE.md` (and Codex's `AGENTS.md`, if Codex is installed) and the blocks you choose in Claude Code's settings. No keys |
| **Approver device** | `./install.sh client --owner --broker-url …` | `valetkey-broker` and `valetkey-entry` |
| **Trusted server** (optional) | `./install.sh client --broker-url … --with-deps` | `valetkey-broker`, the `vault` shim, fnox |

Never combine two rows on one machine, with one exception: the hypervisor hosts the vault, broker and bellhop as separate guests. If you use Proxmox, the first row replaces the next three.

What no installer can do for you, because each step is a decision or a secret:

- edit the tailnet policy in the Tailscale admin console;
- create the Vaultwarden accounts and organizations, and type their passwords;
- sign the broker services in (`valetkey-broker login`) and sign tools in on the bellhop;
- write the routes and the bellhop's tool list: who may use what;
- take `sudo` away from the agent's user.

## Getting started

Allow about an hour for the first setup, most of it creating Vaultwarden accounts. You type every password yourself, into a hidden prompt or Vaultwarden's own web page. None of them goes into this repo, a command argument or a chat.

The [Installer reference](#installer-reference) is the map: each step below runs on a different machine.

### 1. Prepare the tailnet

In the Tailscale admin console ([Tailscale: what valetkey relies on](system-design.md#tailscale-what-valetkey-relies-on) has the documentation links):

1. **DNS:** turn on **MagicDNS** and **HTTPS Certificates**.
2. **Access controls:** merge [`deploy/tailscale/acl.example.hujson`](../deploy/tailscale/acl.example.hujson) into your policy. Approver devices reach `tag:vault` and `tag:broker` on 443; workload machines reach only `tag:broker:443` (and `tag:bellhop:443` if you use the bellhop); the broker reaches `tag:vault:443`; nothing reaches the broker or bellhop over SSH. The `tests` block checks this every time you save.

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
2. Create the three **broker accounts**, each with its own long random password from your password generator. Addresses are only login names; no mail server is needed.

   | Account | Joins | Access |
   |---|---|---|
   | `…-local` | `local-org` | *Can view* on `local-broker-collection` |
   | `…-infra` | `infra-org` | *Can view* on `infra-broker-collection` **and every nested collection** |
   | `…-entry` | both | *Can edit* on both collections **and every nested collection** |

3. As yourself, create the organizations `local-org` and `infra-org` with those collections, and invite the broker accounts. Log in once as each broker account to accept, then **Confirm** each member as yourself.
4. Close sign-ups: `pct exec 210 -- /opt/vaultwarden/close-signups.sh` (or run the script inside the container).

Vaultwarden grants access per collection: a parent does not include its children. Each new project collection (`infra-broker-collection/<project>`) needs the `-infra` and `-entry` accounts granted on it, and then `sudo valetkey-broker login infra …` and `sudo valetkey-broker login entry …` run again on the broker. The broker services record collection IDs at login (`/var/lib/valetkey-broker-<name>/org.json`). Different org or collection names are fine; set them in `/etc/valetkey-broker/vault.env` (step 4).

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

`--with-deps` installs Python, jq, Tailscale, the Bitwarden CLI and fnox (pinned version, via mise), then joins the tailnet as `broker` with `tag:broker`; open the login URL it prints. `install.sh broker` then creates the service users, installs the code to `/usr/local/libexec/valetkey-broker`, writes `/etc/valetkey-broker/{routes,entry}.toml` and `vault.env` from the examples, generates the loopback credential, fetches the Tailscale certificate and enables the units. It prints `ok`, `skip` or `FAIL` for each step. It is safe to re-run, and never overwrites your `/etc/valetkey-broker` files.

Edit the configs for your machines (see [Configuration](operations.md#configuration)):

```sh
sudo nano /etc/valetkey-broker/routes.toml   # who may use what
sudo nano /etc/valetkey-broker/entry.toml    # your own devices (owner_nodes)
sudo nano /etc/valetkey-broker/vault.env     # VAULT_URL and org/collection names, if not the defaults
```

### 5. Log the broker services in

From a secure terminal on the broker (its console, or LAN SSH). Each command asks for that account's password at a hidden prompt:

```sh
sudo valetkey-broker login local <-local account address>
sudo valetkey-broker login infra <-infra account address>
sudo valetkey-broker login entry <-entry account address>
sudo valetkey-broker check        # expect: unlocked ×3, ok for every item and route, all units active
```

### 6. Set up the machines that use keys

**On each agent machine**, one command as root:

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh agent --broker-url https://broker.<tailnet>.ts.net
```

It installs `valetkey-broker` in agent mode, the hourly sweep and a policy that limits MCP servers to the broker, all as root-owned files, and prints what is left for you. With a bellhop, add `--bellhop-url https://bellhop.<tailnet>.ts.net/bellhop --tools tool1,tool2`; each tool name becomes a stand-in. `--user NAME` says which user the agent runs as, and `--no-policy` skips the MCP policy. If T3 Code is installed for that user, the policy also allows T3's own tools; `--no-t3` leaves them out. [T3 Code](t3-code.md)

Then, as the user the agent runs as, set up Claude Code itself:

```sh
./install.sh claude
```

It asks six questions about what to block, shows the changes, and writes them only when you agree. See [Working with AI agents](agent-setup.md#working-with-ai-agents).

**On a Mac that runs agents**, use `./install.sh client --agent --broker-url https://broker.<tailnet>.ts.net` to install the agent CLI and offline guide.

**On the phone or computer where you enter keys**, open the broker’s entry page in your browser. The broker administrator lists this device in `owner_nodes`. If you prefer a hidden terminal prompt on an approved computer, run this from the checkout:

```sh
./install.sh client --owner --broker-url https://broker.<tailnet>.ts.net
```

**Only if an application needs the secret itself**, install the client on the separate machine that runs that application:

```sh
./install.sh client --broker-url https://broker.<tailnet>.ts.net --with-deps
```

For example, a web app that connects directly to a database needs its password. The broker checks both `trusted_value_nodes` and the route for that password. Installing the tools alone grants no access. Keep agents off this server. If all your tools call the broker’s API routes, skip the raw-key project setup below.

Client tools install in `~/.local/bin` by default. On an application server, put that directory before HashiCorp `vault` on `PATH`. The optional `--with-deps` flag installs missing dependencies, including fnox for this role. Run `./install.sh doctor` to check installation and broker reachability.

### 7. Wire up a project

On a separate trusted server with no agents or agent-controlled code, add the project to
`/etc/valetkey-broker/routes.toml` on the broker and reload. Merge the security lists with existing policy:

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
sudo valetkey-broker routes install
```

Point the project's `fnox.toml` at the broker. To generate it from your current `fnox list` (names only, never values):

```sh
{ fnox list; echo "== production"; fnox list -P production --no-defaults; } \
  | valetkey-fnox-to-broker --project shop --env qa --org infra > fnox.toml
```

```toml
[providers.broker]
type    = "vault"
address = "https://broker.<tailnet>.ts.net/infra"   # broker URL + org
path    = "shop"                                    # project
token   = "tailscale"                               # placeholder; identity is the machine

[secrets]
DATABASE_URL = { provider = "broker", value = "database-url-shop-qa", if_missing = "error" }
TLS_KEY      = { provider = "broker", value = "tls-key-shop-qa/notes", if_missing = "error" }
```

Check every item, then run as usual:

```sh
valetkey-broker check infra $(sed -n 's/.* value = "\([^"]*\)".*/\1/p' fnox.toml)    # all ok
FNOX_DAEMON=off fnox exec -- npm run dev
```

Set `FNOX_DAEMON=off` (for example in the project's `.mise.toml` `[env]`) so values aren't cached outside the process. Once it works, delete the old `.env` files and local keys yourself, and rotate them when convenient.

### 8. Add the bellhop (optional)

Only if agents need a command-line tool that keeps its own login. An HTTP API or an MCP server with a fixed key needs no bellhop: give it an `[api.*]` route on the broker.

**On Proxmox** (creates a Debian 13 VM and installs the bellhop in it):

```sh
bash bellhop/proxmox-bellhop-vm.sh                # VMID=212 BELLHOP_HOSTNAME=bellhop by default
```

**On any Debian/Ubuntu VM that runs nothing else:**

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh bellhop
```

Add `tag:bellhop` to the tailnet policy first; the installer joins the tailnet with that tag and prints a login URL. It installs no tool and no login. [bellhop/README.md](../bellhop/README.md) covers listing tools, signing them in and the stand-in command for agent machines.

### 9. Verify

From a client:

```sh
curl -fsS https://vault.<tailnet>.ts.net/alive                          # approver devices only
curl -fsS "https://broker.<tailnet>.ts.net/local/check?names=x"         # {"x": "not-allowed"} or "missing"
```

From a device that isn't on the tailnet, both should fail. On the broker, `sudo valetkey-broker check` should show `unlocked` ×3 and `ok` everywhere.
