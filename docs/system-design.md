# How Valetkey works

## What it is

valetkey is a self-hosted credential broker. It pairs [Vaultwarden](https://github.com/dani-garcia/vaultwarden) and [fnox](https://fnox.jdx.dev) with two parts of its own, the **broker** and the **bellhop**, over [Tailscale](https://tailscale.com). The broker checks which machine is calling and adds the key, so agents call broker API routes without holding one; separate trusted services can opt into raw credentials. The bellhop does the same for command-line tools and MCP servers that keep their own login: they run on a separate bellhop machine, and agents use them by name without ever holding the login.

A valet key starts the car but won't open the trunk. valetkey gives each machine the same kind of key: it can use the few credentials its routes allow, for as long as it is on your tailnet, and nothing else. The master key stays in one place: a small broker VM that is the only thing allowed to unlock the vault.

Software alone does not keep a key from an agent. **Where each part runs matters as much as what it does.** Read [Containment](#containment-where-everything-runs) before installing anything, then follow [secure setup](secure-setup.md). Never run agents on the broker, the bellhop, the vault server, an approver device, a trusted server or their management host. Raw reads are denied by default; existing deployments need the migration steps in that guide before updating valetkey.

## Why

Secrets tend to spread across a fleet: an `.env.local` on the dev box, a key in the laptop's Keychain, an age-encrypted file with its private key next to it, a token pasted into a VM's systemd unit. Every copy is one more place to leak from. Once AI agents run on those machines, every copy is also something an agent can read and repeat back into a chat transcript.

Two things can go wrong with an agent that holds a key, and valetkey reduces the risk of both:

- **The agent goes wrong on its own.** It misreads a task, takes a shortcut or acts on a bad guess, and does something harmful: prints a key into a transcript, commits it, or uses it somewhere it was never meant for.
- **Someone else steers the agent.** A web page, a document, a package or a tool result carries hidden instructions (prompt injection), and the agent follows an attacker's orders instead of yours.

In both cases an agent with no key has nothing to hand over, and it can reach only the routes, operations and tools you listed for its machine. This lowers the risk; it does not remove it. An agent can still misuse whatever access you did give it, as [What containment does not do](#what-containment-does-not-do) explains.

Password managers solve storage. They don't solve *use*: to use a key a process needs the value, so the value ends up on the machine. Secret managers built for that (Vault, Infisical, Doppler) bring tokens of their own, and a token on a client is just another secret to protect.

valetkey takes a different route:

| Problem | valetkey's answer |
|---|---|
| Keys copied onto every machine | Agent machines receive no raw provider key through approved API routes. Separate trusted servers fetch only what their processes need. Existing secret files are not removed by Valet. |
| Broker client tokens to protect and rotate | **Identity is the machine.** The broker asks Tailscale who is calling (`tailscale whois`). There is no additional Valet bearer token; Tailscale identity still needs protecting. |
| Agents that can read and print secrets | Declared agent machines use API routes by default. A Claude setup-token exception exposes that token to the named machine. `valetkey-broker check` reports storage/access status, not upstream authentication. |
| Pasting a new key into chat | The owner types the value into a **hidden terminal prompt or a web page on the tailnet**. The agent picks the name, so naming can't drift. |
| A key that silently expired | **Replace with backup**: the old value is kept as `<name>-backup-YYYYMMDD`. Only trusted servers/operators may use backups. |

## Features

- **No agent-side provider key required.** Approved broker API routes inject keys remotely. Existing credentials and Tailscale machine state still need protection. Revoke route access in `routes.toml` or the tailnet policy.
- **Key injection.** `[api.*]` routes add credentials to outgoing HTTPS calls (exact host, methods and path prefixes) through `fnox proxy`. The calling process never has the key.
- **Works with fnox as is.** Uses fnox's own `vault` provider with a 100-line shim. Keep `fnox exec`, profiles and `if_missing = "error"`.
- **Broker-only agent policy.** Agent nodes can check and request; ordinary raw reads require a separate grant for a trusted server. Includes offline guidance for agent memory.
- **Owner entry, two ways.** Hidden terminal prompt (`valetkey-entry`) or a plain, phone-friendly web page that only your devices can submit to.
- **Replace with backup.** Each replace keeps `<name>-backup-YYYYMMDD`; same-day collisions are refused. Backups require the same trusted raw-consumer policy as the original.
  fnox may find multiple items when a backup contains the original name. The owner must resolve that ambiguity privately before restarting the route; agents must not delete backups. `valetkey-broker check` and `valetkey-broker routes install` report it.
- **Upstream 401/403 detection.** An API route that starts failing marks its item *may be expired*. You see the mark next to a one-click replace, or you can ignore it until the service refuses the key again.
- **Duplicate names.** The entry page shows requests and stored keys whose names differ by one word. You can drop the extra request, or move a key that no route uses to the vault's trash.
- **Least privilege on the broker.** Separate system users, separate Vaultwarden accounts, view-only read accounts, a write account unlocked for seconds at a time, and hardened systemd units (`ProtectSystem=strict`, `NoNewPrivileges`, and more).
- **Names only, everywhere.** Audit logs, checks, errors and installer output contain item names and `ok` / `FAIL`, never values.
- **No runtime dependencies beyond the tools.** Python standard library only. The broker needs `bw`, `fnox`, `jq` and `tailscale`, and `install.sh broker --with-deps` installs them.
- **A bellhop for tools with their own login.** bellhop runs listed subcommands of a command-line tool on a separate machine and returns the output. Login and token subcommands, risky options and paths into the bellhop are refused. Files travel with the command.
- **Install requests, not installs.** An agent asked to add a tool that needs a login files a request; the owner installs it. Nothing an agent sends makes the bellhop install or run anything new.
- **Checks on the agent machine.** A root-owned policy can limit MCP servers to the broker and bellhop, and an hourly sweep reports logins and new tools by name.
- **One-command installs** for all three parts: `install.sh vaultwarden`, `install.sh broker --with-deps` and `install.sh client --with-deps` bring their own Docker, Tailscale, Bitwarden CLI and fnox. Proxmox scripts can create the Vaultwarden container and the broker VM from scratch.

## The parts and where each runs

valetkey is several small tools, each with one job, and each belongs on a particular machine. The machines are separate on purpose; see [Containment](#containment-where-everything-runs).

| Part | What it is for | Runs on |
|---|---|---|
| **Tailscale** | The private network between your machines. It gives every machine a name the broker and bellhop can check, issues the HTTPS certificates, and holds the policy that says who may reach whom. | Every machine |
| **Vaultwarden** | The vault. Stores every key, encrypted. You manage it with the Bitwarden apps. Only the broker and your own devices can reach it. | Vault host |
| **Broker services** | They stand between your machines and the vault: unlock it, hand out only what each machine's routes allow, add keys to outgoing API calls, and take new keys from you. | Broker |
| &nbsp;&nbsp;`valetkey-broker-front` | The only listener: HTTPS on the broker's tailnet address. Passes each request to a service below and tells it which machine is calling. | Broker |
| &nbsp;&nbsp;`valetkey-broker-local`, `valetkey-broker-infra` | The read side, one per vault organization: checks, values for trusted servers, API routes. | Broker |
| &nbsp;&nbsp;`valetkey-broker-entry` | The write side: named requests from agents, and the page and prompt where you type a value. | Broker |
| **fnox** | On the broker, adds the key to an outgoing API call (`fnox proxy`). On a trusted server, starts a program with its secrets (`fnox exec`). | Broker; trusted servers |
| **Broker tools** `valetkey-broker login`, `valetkey-broker check`, `valetkey-broker routes`, `valetkey-broker update` | Sign the broker services in to the vault, check them, apply route changes, update the broker. | Broker, at its console |
| **`valetkey-broker`** | The client. Asks the broker whether a key exists and requests a new one by name. Never shows a value. | Agent machines and every other client |
| **`vault`** | A small shim that lets fnox fetch a value from the broker for a trusted program. | Trusted servers only |
| **`valetkey-entry`** | A hidden prompt for typing a key's value. | Approver devices only |
| **Bellhop service** | Runs command-line tools that keep their own login, on behalf of agent machines, within a list you write. | Bellhop |
| **`valetkey-bellhop`** | The stand-in. Sends a command and its files to the bellhop and returns the output. Linked under each tool's own name. | Agent machines |
| **sweep** | An hourly check that no login and no unexpected tool has appeared. Reports names only. | Agent machines, as root |
| **Agent rules** | Standing instructions that tell an agent how to ask for keys and tools, and what never to do. | Agent machines |
| **Proxmox scripts** | Create the vault container and the broker and bellhop VMs, then run the right installer in each. | Hypervisor |

## How it works

```text
 callers, over the tailnet            broker (Linux VM, TPM)
┌───────────────────────────┐        ┌──────────────────────────────────────────────┐
│ agent machine             │        │ valetkey-broker-front :443 (Tailscale certificate)     │       ┌─────────────────────┐
│   valetkey-broker, API    │───────▶│   /local → valetkey-broker-local  :8441 ─┐             │──────▶│ Vaultwarden         │
│   and MCP: never a key    │        │   /infra → valetkey-broker-infra  :8442 ─┼─ read       │       │   local-org         │
├───────────────────────────┤        │   /entry → valetkey-broker-entry  :8443 ─┘  and write  │       │   infra-org         │
│ trusted server            │───────▶│                                              │       └─────────────────────┘
│   fnox → vault shim       │        │ fnox proxy: adds the key to [api.*] calls    │       ┌─────────────────────┐
├───────────────────────────┤        │ valetkey-broker-approval: authenticator, route grants  │──────▶│ service APIs and    │
│ approver device           │───────▶│ valetkey-broker-entry: key entry; checks what a stored │       │ MCP servers         │
│   valetkey-entry, browser │        │   key opens on its MCP routes                │       └─────────────────────┘
└───────────────────────────┘        └──────────────────────────────────────────────┘
 identity = Tailscale machine name   each service: own Linux user, own Vaultwarden account,
 nothing stored on an agent machine  own encrypted unlock, own audit log
```

**The vault.** Vaultwarden, published only on the tailnet with `tailscale serve`. Keys are sorted into two organizations by *where they are used*:

- **`local`**: your own machines and network (home lab, personal tools, push URLs).
- **`infra`**: deployed and cloud services, with one nested collection per project (`infra-broker-collection/shop`).

**The broker.** One small Linux VM, and the only machine that unlocks a vault. It runs five systemd services, each as its own system user:

| Service | Vault access | Does |
|---|---|---|
| `valetkey-broker-front` | none | TLS on the broker's Tailscale address, port 443. Routes `/local`, `/infra` and `/entry` to the loopback services and stamps each request with the caller's tailnet IP. |
| `valetkey-broker-local` | reads `local-org` (view-only account) | Serves `local` values, checks and API routes. |
| `valetkey-broker-infra` | reads `infra-org` (view-only account) | The same for `infra`. |
| `valetkey-broker-entry` | writes both (edit account) | Creates and replaces items, only when the owner submits a value. Unlocks for one write, then locks again. Then checks what the new key opens on its MCP routes and shows the owner. |
| `valetkey-broker-approval` | none | Pairs your authenticator at the console and approves proposed `[api.*]` routes with its codes. |

Each unlock password is stored with `systemd-creds`, encrypted to the VM's TPM/vTPM and host key. Read services keep a decrypted item cache in broker memory that refreshes every 5 minutes, and immediately after every write.

**Who may use what.** `/etc/valetkey-broker/routes.toml` on the broker lists, per credential, which Tailscale machines (`nodes`) may use it. There are three kinds of route:

| Route | The caller gets | Use it for |
|---|---|---|
| `[api.<route>]` | The upstream response. The broker forwards the call and `fnox proxy` injects the key for that host, those methods and those paths. | Reviewed HTTPS endpoints that cannot return credentials. |
| `[project.<name>]` | Values of that project's items, read through fnox at process start. | Apps you run with `fnox exec`. |
| `[value.<name>]` | One raw value. | Things that must hold the value themselves: SSH keys, push URLs, database URLs. |

An unknown caller (not a tailnet peer) gets `403`. A tailnet machine that isn't listed gets `not-allowed`. There is no endpoint that lists the vault.

Agent machines belong in `[security].agent_nodes`. Ordinary raw reads require a matching
route and membership in `trusted_value_nodes`. Owners can explicitly grant a machine
its own Claude subscription token. See [why this exception exists](security-boundaries.md#should-an-agent-machine-hold-its-own-model-credential) and [how to configure it](secure-setup.md#claude-code-subscription-sign-in).

**Three flows.**

1. **Pull.** A project's `fnox.toml` points fnox's `vault` provider at the broker. fnox runs the `vault` shim for each secret, the shim does one HTTPS GET, and the value goes into the process's environment and nowhere else. `fnox exec -- npm run dev` works as before.
2. **Inject.** A client calls `https://broker.<tailnet>.ts.net/local/stripe-shop/v1/…` with no key. The broker adds the key on the way out. The client never holds it.
3. **Entry.** An agent runs `valetkey-broker request stripe-api-key-shop-prod --org infra --project shop --purpose "…"`. You enter the value with `valetkey-entry <id>` in your private terminal or on `https://broker.<tailnet>.ts.net/entry`, on a separate approver device. The broker writes it, verifies it and tells the agent `stored`; the response contains no value.

**The bellhop (optional).** Some tools cannot go through an API route: a command-line tool that signs in and keeps its own login file, for example. valetkey puts those on a second small VM, the bellhop. The agent machine has a stand-in command under the tool's name; it sends the command and any files to the bellhop, which runs the real tool with its own login and returns the output. See [The bellhop](agent-setup.md#the-bellhop-tools-with-their-own-login).

More detail: [docs/architecture.md](architecture.md).

## Containment: where everything runs

The rule is short: **a key lives only on a machine where no agent runs, and an agent machine holds no key.** Everything below follows from it. The broker and the bellhop identify a caller by its machine, not by the program that is calling, so every process on a machine shares that machine's access. An agent and a key on the same machine means the agent can reach the key, whatever the software says.

### The machines

| Machine | Holds | Runs agents | Reachable by |
|---|---|---|---|
| **Agent machine** (dev box, bot, CI runner) | No key, no login, no vault session | Yes | Nothing needs to reach it |
| **Broker** | Vault unlock material; keys in memory | Never | Agent machines and approver devices, port 443 only |
| **Bellhop** (optional) | Logins of the command-line tools it runs | Never | Agent machines, port 443 only |
| **Vault** (Vaultwarden) | The encrypted vault | Never | The broker and approver devices, port 443 only |
| **Approver device** (your laptop, phone) | What you type during entry | Never | Nothing |
| **Trusted server** (a deployed service) | Only the keys its own process needs | Never | Its own application port |
| **Hypervisor** | Control of every VM on it | Never | You, at its console |

### Recommended layout

```
 separate hardware                          one hypervisor host (Proxmox VE)
┌──────────────────────┐   tailnet 443    ┌───────────────────────────────────────────┐
│ agent machines       │ ───────────────▶ │ VM   broker    broker services, fnox, vTPM │──┐
│  no keys, no sudo,   │ ───────────────▶ │ VM   bellhop   optional                   │  │ 443
│  sandboxed commands  │                  │ LXC  vault     Vaultwarden in Docker      │◀─┘
└──────────────────────┘                  └───────────────────────────────────────────┘
┌──────────────────────┐   tailnet 443            ▲ console or LAN SSH only
│ approver devices     │ ───▶ vault, broker       │
└──────────────────────┘                    you, never an agent
```

- **Put the broker, the bellhop and the vault on a hypervisor that no agent can administer.** A small always-on box running [Proxmox VE](https://www.proxmox.com/proxmox-virtual-environment) is the tested path: `deploy/proxmox/` and `bellhop/proxmox-bellhop-vm.sh` create all three from scratch.
- **Keep agent machines on different hardware**, or at least on a different hypervisor. An agent that controls a hypervisor controls every VM on it.
- **Give each of the three its own VM or container.** The broker gets a VM because it needs a virtual TPM. Run nothing else on any of them.
- **Manage the broker and the bellhop from the hypervisor's console or LAN SSH.** Nothing reaches them over SSH on the tailnet, and there is no remote or automatic update trigger.

### What counts as separate

| Arrangement | Good enough? |
|---|---|
| Different physical machines | Yes. The strongest option |
| VMs on a dedicated hypervisor that agents cannot log in to or control | Yes |
| VMs or containers created by a desktop tool on a computer that also runs agents | **No** |
| A container next to the agent, when the agent can reach the container engine | **No** |
| Another Unix user on the agent's machine | **No** |
| A rule in the agent's instructions | **No**. Useful, but not a boundary |

Desktop virtualization is built for convenience, and that convenience is the problem. Check what yours shares before trusting it. With [OrbStack](https://orbstack.dev)'s default machines, for example, each machine can read the host's home folder, see the other machines' files and run commands on the host, the host can run commands as root inside any machine, and the default user has passwordless sudo. Its isolated machines turn the file sharing and host integration off, which makes them fine for *agent* machines and for testing. They are still not a place for the broker or the bellhop if agents also run on that computer.

Things that quietly join two machines into one, from the agent's point of view:

- a mounted host folder or a shared volume;
- the host's Tailscale identity shared by several VMs (give each agent machine its own node);
- passwordless `sudo`, or membership of the `docker` group;
- a forwarded SSH agent, a shared clipboard, a management socket;
- backups or disk images of the broker or bellhop stored where an agent can read them.

### The agent machine

An agent machine is treated as readable by the agent in full. Set it up so there is nothing to read:

1. **Its own tailnet identity, tagged.** A tagged machine gets only the grants written for that tag. A personal device may inherit access meant for your own machines.
2. **No sudo for the agent's user**, and no control of its own hypervisor or host.
3. **No logins.** No `gh auth login`, no cloud CLIs signed in, no `.env` files with real keys, no SSH keys that open other machines. Git goes [through the broker](git-through-the-broker.md).
4. **Sandbox the agent's commands** where the agent supports it, with network access limited to the hosts it needs. This limits accidents; it does not replace the points above.
5. **Restrict where MCP servers may live.** With Claude Code, a root-owned `/etc/claude-code/managed-settings.json` can allow only servers at the broker's or the bellhop's address.
6. **Watch for drift.** `bellhop/sweep.py`, run hourly as root, reports any login file, key in a settings file or newly installed program on the machine, by name only.
7. **Do not sign in to things on it yourself** out of habit. That is how logins come back.

### The network

Merge [`deploy/tailscale/acl.example.hujson`](../deploy/tailscale/acl.example.hujson) into your tailnet policy. It grants exactly this and tests it on every save:

| From | To | Port |
|---|---|---|
| Agent machines | broker, bellhop | 443 |
| Approver devices | vault, broker | 443 |
| Broker | vault | 443 |
| Anything | broker or bellhop over SSH | Denied |
| Agent machines | vault, approver devices | Denied |

**Keep all of it inside the tailnet.** valetkey is not intended for a public-facing system. Do not publish the broker, the bellhop or the vault with Tailscale Funnel, a port forward, or a reverse proxy, tunnel or public IP address that is reachable from outside the tailnet (`tailscale serve`, which the vault setup uses, stays inside it), and do not share those machines into someone else's tailnet. The broker decides who is calling from the machine's Tailscale identity and has no other sign-in, so anything that lets outside traffic reach it removes the protection.

Grants in a tailnet policy add up, so remove any allow-all or member-to-member rule. The tailnet policy cannot close a LAN or public interface: the broker and bellhop listen only on their tailnet address, with their services on loopback behind it.

### Recommended tools

| Job | Tool | Why |
|---|---|---|
| Hypervisor for broker, bellhop and vault | Proxmox VE on its own box | VMs with a virtual TPM, containers, a console that does not depend on the tailnet; scripts included |
| Guest OS | Debian 13 (cloud image) | Current systemd for `systemd-creds`; what the scripts and tests use |
| Network and machine identity | Tailscale with MagicDNS, HTTPS certificates and tags | The broker and bellhop name callers with `tailscale whois`; tags keep grants narrow |
| Vault | Vaultwarden | Self-hosted, works with the Bitwarden apps and CLI |
| Key injection and project secrets | fnox | The broker's API routes use `fnox proxy`; projects keep `fnox exec` |
| Unlock material at rest | `systemd-creds` with the VM's vTPM | Encrypted to that VM; useless if the disk is copied |
| Monitoring | Any uptime monitor on its own tagged machine | Checks that the broker and bellhop answer; it needs port 443 and no route |
| Agent sandbox | The agent's own sandbox and policy file | Limits what a command can read and reach on the agent machine |

One command does the parts that can be automated: `sudo ./install.sh agent …` installs the clients as root-owned files the agent cannot edit, the MCP policy (point 5) and the sweep with its hourly timer (point 6). It then lists what is left for you: the tailnet identity, `sudo`, and keeping logins off the machine.

### What containment does not do

Hiding a key stops it being copied. It does not limit what an agent does with the access it is given: a route or a listed tool can be used freely by every listed machine. Keep keys narrow at the service, prefer test-mode and capped keys, and list only the operations you need. See [Security model](security-boundaries.md#security-model).

## Tailscale: what valetkey relies on

valetkey has no passwords or tokens of its own between machines. It uses Tailscale for the network, for naming the caller and for certificates, so the tailnet's settings are part of the security of the whole setup.

| Tailscale feature | What valetkey uses it for | Documentation |
|---|---|---|
| Install | Every machine joins the tailnet. Each agent machine needs its own node, not a host's | [Install on Linux](https://tailscale.com/kb/1031/install-linux) |
| MagicDNS | Names such as `broker.<tailnet>.ts.net` | [MagicDNS](https://tailscale.com/kb/1081/magicdns) |
| HTTPS certificates | Certificates for the broker, bellhop and vault, fetched with `tailscale cert` and renewed weekly | [Enabling HTTPS](https://tailscale.com/kb/1153/enabling-https) |
| Policy file | Who may reach whom, on which port | [ACLs](https://tailscale.com/kb/1018/acls), [Grants](https://tailscale.com/kb/1324/grants) |
| Policy tests | A saved policy is rejected if it opens something it should not | [Policy syntax: tests](https://tailscale.com/kb/1337/policy-syntax#tests) |
| Tags | `tag:vault`, `tag:broker`, `tag:bellhop`, and a tag for agent machines | [Tags](https://tailscale.com/kb/1068/tags) |
| `tailscale whois` | How the broker and bellhop learn which machine is calling | [CLI reference](https://tailscale.com/kb/1080/cli) |
| Tailscale Serve | Publishes Vaultwarden on the tailnet and nowhere else | [Serve](https://tailscale.com/kb/1312/serve) |
| Key expiry | A server whose key expires drops off the tailnet | [Key expiry](https://tailscale.com/kb/1028/key-expiry) |
| Auth keys (optional) | Joining a machine without opening a login link. An auth key is a secret: treat it like one | [Auth keys](https://tailscale.com/kb/1085/auth-keys) |
| Tailnet Lock (optional) | New machines must be signed by one you trust before they can join | [Tailnet Lock](https://tailscale.com/kb/1226/tailnet-lock) |

Set it up in this order:

1. In the admin console, under DNS, turn on **MagicDNS** and **HTTPS Certificates**.
2. Merge [`deploy/tailscale/acl.example.hujson`](../deploy/tailscale/acl.example.hujson) into your policy: the `tagOwners`, the `grants` and the `tests`. Remove any allow-all rule you had.
3. Only then run the installers. Each one joins its machine with the right tag and prints a login link.

Things that catch people out:

- **A tag must be in `tagOwners` before a machine can join with it.** Otherwise the join is refused.
- **A test opens nothing.** `tests` only check; access comes from `grants`. Each goes in its own section of the policy.
- **Tag names must match exactly.** A grant for `tag:monitor` does nothing for a machine tagged `tag:monitoring`.
- **A machine that may not reach a peer may not be able to resolve its name either.** A name that does not resolve usually means a missing grant, not a DNS fault.
- **Tagged machines are not yours in the policy's eyes.** That is the point for servers and agent machines. It also means a tagged machine can never be an approver device.
- **VMs behind one host share that host's identity** unless each runs Tailscale itself. The broker would then treat them as one machine.
- **The broker and bellhop know a machine by its Tailscale name.** Renaming a machine changes who it is; update the routes when you do.
- **The policy covers the tailnet only.** It cannot close a LAN or public address. The broker and bellhop listen only on their tailnet address.
