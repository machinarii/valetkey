# Install valetkey

valetkey runs on two kinds of machines:

- **Your devices:** the machines where your agents run, the device where you type keys, and any
  server whose app must read a key itself. Install these with **Homebrew**, on macOS or Linux.
- **Three small Linux servers** that hold the keys and the tools: the vault, the broker and,
  optionally, the desk. Install these with the **installer**.

Put each role on its own machine; [Containment](system-design.md#containment-where-everything-runs)
explains why. Every machine needs Tailscale; see [Requirements](#requirements).

## Your devices: Homebrew

Add valetkey's Homebrew tap once on each device:

```sh
brew tap machinarii/valetkey https://github.com/machinarii/valetkey
brew trust machinarii/valetkey
```

Then install the package for that machine's role, and point it at your broker:

| This machine | Install | Then |
|---|---|---|
| **Runs AI agents** (Claude Code, Codex, …) | `brew install valetkey-agent` | `valetkey-relay set-url https://broker.<tailnet>.ts.net`, then `valetkey-agent-setup`: valetkey's rules and settings for Claude Code and Codex, asking before each change |
| **Where you type keys** and approve routes | `brew install valetkey-approver` | `valetkey-relay set-url https://broker.<tailnet>.ts.net` |
| **A server whose app reads a key itself** (optional) | `brew install valetkey-trusted-server` | `valetkey-relay set-url https://broker.<tailnet>.ts.net`, and install [fnox](https://fnox.jdx.dev) |

- One machine, one role: the packages refuse to install side by side.
- `brew upgrade` keeps them current.
- A phone or tablet where you type keys needs nothing installed: open the broker's entry page in
  its browser.
- On a **Linux agent machine**, `sudo ./install.sh agent` does more than the package: it also adds
  the hourly sweep, the desk's tool stand-ins and a root-owned Claude Code policy. Use it instead of
  Homebrew there if you want those ([Installer reference](#installer-reference)).

## The servers: the installer

On a **Proxmox VE host**, one command creates the vault, broker and desk and installs each one:

```sh
git clone https://github.com/machinarii/valetkey.git && cd valetkey
sudo ./install.sh proxmox          # add --no-desk to skip the desk
```

Without Proxmox, run this on each Linux server and choose **Vault host**, **Broker** or **Desk**:

```sh
curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash
```

The installer asks for `sudo` only when it needs it, fills in your tailnet's addresses, and never
asks for a key or password. To read the code first, clone the repository and run `./install.sh`.

### Debian and Ubuntu: apt

On Debian or Ubuntu you can get the same installer as a signed package instead of `curl | bash`.
apt checks the signature on every install and upgrade:

```sh
curl -fsSL https://machinarii.github.io/valetkey/apt/valetkey.gpg | sudo tee /usr/share/keyrings/valetkey.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/valetkey.gpg] https://machinarii.github.io/valetkey/apt ./" \
  | sudo tee /etc/apt/sources.list.d/valetkey.list
sudo apt-get update && sudo apt-get install valetkey
valetkey-setup                     # choose this machine's role
```

The package installs one command, `valetkey-setup`, and the installer's files in
`/usr/share/valetkey`. It works for every role on Linux: agent machine, approver device, trusted
server, broker, desk and vault host. Installing or upgrading the package changes nothing on its
own. After `sudo apt-get upgrade`, run `valetkey-setup` again to apply the new version to the
machine's role (on the broker, `sudo valetkey-relay update` remains the way to update).

### Fresh installation on Proxmox VE

`sudo ./install.sh proxmox` checks guest IDs across the cluster before creating the vault container
(210), broker VM (211) and desk VM (212), then runs each guest's installer. To choose other IDs, run
for example `sudo env CTID=310 BROKER_VMID=311 DESK_VMID=312 ./install.sh proxmox`. Authorize each
guest to join your tailnet when asked. If one guest fails, the others stay in place; fix that guest
before continuing.

## Then set it up

Installing puts the programs in place; the setup is where you decide who may use what. Follow
[Getting started](#getting-started), in this order:

1. [Prepare the tailnet](#1-prepare-the-tailnet)
2. [Run Vaultwarden](#2-run-vaultwarden) on the vault host
3. [Create the accounts and organizations](#3-create-the-accounts-and-organizations)
4. [Install the broker](#4-install-the-broker)
5. [Log the relays in](#5-log-the-relays-in)
6. [Set up the machines that use keys](#6-set-up-the-machines-that-use-keys)
7. [Wire up a project](#7-wire-up-a-project)
8. [Add the desk](#8-add-the-desk-optional) (optional)
9. [Verify](#9-verify)

Already installed? See [Updating valetkey](updating-valetkey.md).

## Requirements

| Where | Needs |
|---|---|
| **Tailnet** | Tailscale with MagicDNS and HTTPS Certificates turned on. You need admin access to edit the ACL. |
| **Vaultwarden** | Any host with Docker (the Proxmox LXC script is optional). Vaultwarden 1.37.2+ for current Bitwarden clients. |
| **Broker** | Debian 12+ or Ubuntu 24.04+ with systemd ≥ 250, Python ≥ 3.11 and a TPM or vTPM (for `systemd-creds`). 2 vCPU, 2 GB RAM and 8 GB disk. Each relay runs the Bitwarden CLI, which is heavy: 1 GB runs out once an org has more than a few `[api.*]` routes. |
| **Desk** (optional) | Debian 12+ or Ubuntu 24.04+ with systemd and Python ≥ 3.11, on its own VM. 2 vCPU, 2 GB RAM and 16 GB disk is enough for a few tools. No TPM needed. |
| **Clients** | Python 3.9+ on `PATH` and Tailscale access to the broker for broker commands. Trusted servers that pull project secrets also need fnox. |

On Linux, `./install.sh client --with-deps` can install Tailscale and, for a trusted server, fnox. On macOS, install Tailscale separately and join the tailnet.

The broker installer checks for the Bitwarden CLI (`bw`), fnox, Tailscale, `jq`, `systemd-creds` and `systemd-run`. On supported Debian and Ubuntu systems, `sudo ./install.sh broker --with-deps` installs the broker dependencies, including `qrencode` for authenticator pairing. See [version-command requirements](updating-valetkey.md) and [model launcher requirements](model-api-accounts.md#requirements) for those workflows.

## Installer reference

What the installer runs for each role, if you'd rather name the role yourself. Run it on that machine, from a copy of this repository; `./install.sh` with no arguments asks instead.

| Machine | Command | What it installs |
|---|---|---|
| **Hypervisor** (Proxmox) | `sudo ./install.sh proxmox [--no-desk]`<br>(runs `deploy/proxmox/vaultwarden-ct.sh`, `deploy/proxmox/broker-vm.sh` and `bellhop/proxmox-desk-vm.sh`) | Creates the vault container and the broker and desk VMs, and runs the three installers below inside them |
| **Vault host** | `sudo ./install.sh vaultwarden` | Docker, Tailscale, Vaultwarden published on the tailnet only |
| **Broker** | `sudo ./install.sh broker --with-deps` | Tailscale, Bitwarden CLI, fnox, the relay services and broker tools |
| **Desk** (optional) | `sudo ./install.sh desk` | Tailscale, the desk service, the TLS front door. No tools, no logins |
| **Agent machine** | `sudo ./install.sh agent --relay-url … --desk-url … --tools …`<br>`./install.sh claude` | `valetkey-relay` in agent mode, the `bellhop` stand-in, the sweep and its timer, a policy that limits MCP servers to the broker and desk (it says why and asks first). Then, as the agent's user, the rules in `CLAUDE.md` (and Codex's `AGENTS.md`, if Codex is installed) and the blocks you choose in Claude Code's settings. No keys |
| **Approver device** | `./install.sh client --owner --relay-url …` | `valetkey-relay` and `valetkey-entry` |
| **Trusted server** | `./install.sh client --relay-url … --with-deps` | `valetkey-relay`, the `vault` shim, fnox |

Never combine two rows on one machine, with one exception: the hypervisor hosts the vault, broker and desk as separate guests. If you use Proxmox, the first row replaces the next three.

What no installer can do for you, because each step is a decision or a secret:

- edit the tailnet policy in the Tailscale admin console;
- create the Vaultwarden accounts and organizations, and type their passwords;
- sign the relays in (`valetkey-relay login`) and sign tools in on the desk;
- write the routes and the desk's tool list: who may use what;
- take `sudo` away from the agent's user.

## Getting started

Allow about an hour for the first setup, most of it creating Vaultwarden accounts. You type every password yourself, into a hidden prompt or Vaultwarden's own web page. None of them goes into this repo, a command argument or a chat.

The [Installer reference](#installer-reference) is the map: each step below runs on a different machine.

### 1. Prepare the tailnet

In the Tailscale admin console ([Tailscale: what valetkey relies on](system-design.md#tailscale-what-valetkey-relies-on) has the documentation links):

1. **DNS:** turn on **MagicDNS** and **HTTPS Certificates**.
2. **Access controls:** merge [`deploy/tailscale/acl.example.hujson`](../deploy/tailscale/acl.example.hujson) into your policy. Approver devices reach `tag:vault` and `tag:broker` on 443; workload machines reach only `tag:broker:443` (and `tag:desk:443` if you use the desk); the broker reaches `tag:vault:443`; nothing reaches the broker or desk over SSH. The `tests` block checks this every time you save.

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

Vaultwarden grants access per collection: a parent does not include its children. Each new project collection (`infra-broker-collection/<project>`) needs the `-infra` and `-entry` accounts granted on it, and then `sudo valetkey-relay login infra …` and `sudo valetkey-relay login entry …` run again on the broker. The relays record collection IDs at login (`/var/lib/relay-<name>/org.json`). Different org or collection names are fine; set them in `/etc/relay/vault.env` (step 4).

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
sudo valetkey-relay login local <-local account address>
sudo valetkey-relay login infra <-infra account address>
sudo valetkey-relay login entry <-entry account address>
sudo valetkey-relay check        # expect: unlocked ×3, ok for every item and route, all units active
```

### 6. Set up the machines that use keys

**On each agent machine**, one command as root:

```sh
git clone https://github.com/machinarii/valetkey && cd valetkey
sudo ./install.sh agent --relay-url https://broker.<tailnet>.ts.net
```

It installs `valetkey-relay` in agent mode, the hourly sweep and a policy that limits MCP servers to the broker, all as root-owned files, and prints what is left for you. With a desk, add `--desk-url https://desk.<tailnet>.ts.net/desk --tools tool1,tool2`; each tool name becomes a stand-in. `--user NAME` says which user the agent runs as, and `--no-policy` skips the MCP policy. If T3 Code is installed for that user, the policy also allows T3's own tools; `--no-t3` leaves them out. [T3 Code](t3-code.md)

Then, as the user the agent runs as, set up Claude Code itself:

```sh
./install.sh claude
```

It asks six questions about what to block, shows the changes, and writes them only when you agree. See [Working with AI agents](agent-setup.md#working-with-ai-agents).

**On a Mac that runs agents**, use `./install.sh client --agent --relay-url https://broker.<tailnet>.ts.net` to install the agent CLI and offline guide.

**On the phone or computer where you enter keys**, open the broker’s entry page in your browser. The broker administrator lists this device in `owner_nodes`. If you prefer a hidden terminal prompt on an approved computer, run this from the checkout:

```sh
./install.sh client --owner --relay-url https://broker.<tailnet>.ts.net
```

**Only if an application needs the secret itself**, install the client on the separate machine that runs that application:

```sh
./install.sh client --relay-url https://broker.<tailnet>.ts.net --with-deps
```

For example, a web app that connects directly to a database needs its password. The broker checks both `trusted_value_nodes` and the route for that password. Installing the tools alone grants no access. Keep agents off this server. If all your tools call the broker’s API routes, skip the raw-key project setup below.

Client tools install in `~/.local/bin` by default. On an application server, put that directory before HashiCorp `vault` on `PATH`. The optional `--with-deps` flag installs missing dependencies, including fnox for this role. Run `./install.sh doctor` to check installation and broker reachability.

### 7. Wire up a project

On a separate trusted server with no agents or agent-controlled code, add the project to
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
sudo valetkey-relay routes install
```

Point the project's `fnox.toml` at the broker. To generate it from your current `fnox list` (names only, never values):

```sh
{ fnox list; echo "== production"; fnox list -P production --no-defaults; } \
  | valetkey-fnox-to-relay --project shop --env qa --org infra > fnox.toml
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
valetkey-relay check infra $(sed -n 's/.* value = "\([^"]*\)".*/\1/p' fnox.toml)    # all ok
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
curl -fsS https://vault.<tailnet>.ts.net/alive                          # approver devices only
curl -fsS "https://broker.<tailnet>.ts.net/local/check?names=x"         # {"x": "not-allowed"} or "missing"
```

From a device that isn't on the tailnet, both should fail. On the broker, `sudo valetkey-relay check` should show `unlocked` ×3 and `ok` everywhere.
