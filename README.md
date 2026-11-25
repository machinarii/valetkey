<p align="center">
  <img src="docs/images/valetkey-icon.png" width="128" height="128" alt="valetkey icon: a smiling robot valet in a green cap holds up a gold key, on a blue tile">
</p>

<h1 align="center">valetkey</h1>

<h2 align="center">Your agents use the key, but they never see it.</h2>

<p align="center">
  <b>A self-hosted credential broker for AI agents.</b><br>
  Give an AI agent access to a tool while the credentials stay on a separate machine:<br>
  a credential broker and a tool bellhop on their own machines, joined over your private Tailscale network.
</p>

<p align="center">
  <img alt="Python: standard library only" src="https://img.shields.io/badge/python-standard%20library%20only-3776ab?style=flat-square&logo=python&logoColor=white">
  <img alt="Self-hosted with Proxmox, Tailscale and Vaultwarden" src="https://img.shields.io/badge/self--hosted-Proxmox%20%C2%B7%20Tailscale%20%C2%B7%20Vaultwarden-1d4e6e?style=flat-square">
  <img alt="Clients: Linux and macOS" src="https://img.shields.io/badge/clients-Linux%20%C2%B7%20macOS-2d81ff?style=flat-square">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-3a3a3a?style=flat-square">
  <img alt="Status: 0.6.0, for personal use" src="https://img.shields.io/badge/status-0.6.0%20%C2%B7%20for%20personal%20use-d69e2e?style=flat-square">
</p>

<p align="center">
  <a href="#get-started"><b>Install valetkey</b></a> ·
  <a href="docs/secure-setup.md">Secure setup</a> ·
  <a href="#guides">Guides</a>
</p>

<br>

<p align="center">
  <img src="docs/images/how-it-works.svg" width="860" alt="Animated diagram with a legend: a green dot is a request or an answer with no key in it, a yellow dot is a master key, a blue dot is a tool's own login. An agent machine that holds no keys and no logins sends the broker a request with no key. The broker checks the calling machine and takes that route's master key from the vault; fnox adds the key and forwards the request to a service API or CLI. The answer comes back to the agent without the key. For a tool with its own login, the agent calls the bellhop, which runs the MCP server or tool with that login and returns only the output. The vault, the broker and the bellhop sit in an area marked: no agent runs here.">
  <br>
  <sub>Agents send requests with no key in them. The broker adds the master key on the way out; the bellhop runs tools that keep their own login.</sub>
</p>

> [!WARNING]
> **For a private tailnet only, and at your own risk.** Never expose the broker, bellhop or vault to
> the internet. valetkey reduces the risk of agents reading your keys; it does not stop an agent
> misusing an operation you approved, and it has had no independent security audit.
> [Security limits](docs/security-boundaries.md)

<p align="center">
  <a href="#what-valetkey-is">What it is</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#prerequisites">Prerequisites</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="#updating-valetkey">Updating</a> ·
  <a href="#guides">Guides</a> ·
  <a href="#status">Status</a> ·
  <a href="#license-and-credits">License</a>
</p>

<br>

<table>
  <tr>
    <td width="25%" valign="top">
      <h3>🔑 No keys on agent machines</h3>
      An agent calls a broker route with no key. The broker checks the calling machine, method and path, adds the key from Vaultwarden and forwards the request.
    </td>
    <td width="25%" valign="top">
      <h3>🛎️ A bellhop for tools that sign in</h3>
      Command-line tools that keep their own login run on a separate bellhop machine. Agents use them by name and get the output, never the login.
    </td>
    <td width="25%" valign="top">
      <h3>🧱 Containment first</h3>
      Every part has its own machine and one installer. Agents run apart from the broker, vault, bellhop and the devices where you enter keys.
    </td>
    <td width="25%" valign="top">
      <h3>🙋 You stay in charge</h3>
      Agents ask for keys and routes by name. You type the value, or approve a route with an authenticator code, on your own approver device.
    </td>
  </tr>
</table>

<br>

## What valetkey is

valetkey is a **credential broker for AI agents**, a kind of agent security infrastructure. It works
with the agent harness you already use, such as Claude Code, Codex and/or T3 Code, and does not
replace it.

| Part | Role | What it does |
|---|---|---|
| broker | Credential-injecting proxy, "secretless" access | Agents call a service through it with no key; it checks the calling machine and adds the key on the way out. It also serves the key entry page and route approvals. On agent machines, the `valetkey-broker` command calls it, requests keys and reports their status. |
| fnox | Secret injector | Runs inside the broker and puts the key into each outgoing request. |
| vault | Secrets manager (Vaultwarden) | Keeps every key, encrypted, on its own machine. Agents never reach it; the broker reads from it. |
| bellhop | Remote tool runner | Runs command-line tools and MCP servers that keep their own login, on a separate machine. |
| MCP routes | MCP gateway | broker routes to MCP servers that need a fixed key: agents connect with no key, and you see which tools the key opens. |
| Rules, policy & sweep | Agent guardrails | Teach agents the workflow, limit MCP servers and report logins that appear on agent machines. |

## How it works

1. **APIs:** the agent calls a route on the broker. The broker checks the calling machine, HTTP
   method and path, then adds the key from Vaultwarden and forwards the request.
2. **Tools with their own login:** the optional bellhop runs approved commands and returns their
   results.
3. **New keys:** an agent requests the item by name, and you enter the key on your **approver
   device**, a phone or computer where you can also approve requested routes. Keep agents off this
   device. Use the broker's entry page in your browser or a hidden prompt in your terminal.
4. **Models:** Claude Code and Codex can use their subscriptions or API credits. For API billing,
   valetkey keeps the Anthropic, OpenAI or OpenRouter key on the broker. Memory capture with
   **claude-mem is optional**.

**Claude's own model login is a deliberate exception.** For unattended subscription use, the owner
can grant one VM its own Claude setup-token. Any process on that VM can copy it, and removing the
grant cannot recall a copy. Native Claude web login is also available. For subscriptions, Codex uses
its own ChatGPT sign-in and has no corresponding valetkey grant.
[Why the subscription terms shaped this choice](docs/security-boundaries.md#should-an-agent-machine-hold-its-own-model-credential)

## Prerequisites

**Hardware: a machine where no agent runs.** The vault and the broker hold your keys, so they
must sit on hardware your agents cannot log in to or administer. A mini PC such as an Intel NUC, a
Raspberry Pi, a spare laptop or a home server will do. A second user account or a container on the
machine where agents run does not count: everything on that machine shares its network identity.

The vault, the broker and the optional bellhop each need a system of their own. Either:

- **one machine with a hypervisor** that runs them as separate guests. Proxmox VE is the tested
  path, and one command creates all three; or
- **one small machine for each**, for example two Raspberry Pis for the vault and the broker.

The broker wants 2 CPU cores, 2 GB of memory, 8 GB of disk and a TPM or virtual TPM, which
protects the vault login it keeps on disk. Most mini PCs have a TPM and a Proxmox VM gets a virtual
one. A Raspberry Pi has none, so there the login is protected only by a key on the same disk; the
Pi (arm64) is also not a tested path yet.

**Software and accounts:**

| Where | Needs |
|---|---|
| **Every machine** | [Tailscale](https://tailscale.com), on one tailnet with MagicDNS and HTTPS certificates turned on. You need admin access to edit its access rules. |
| **vault** | Linux with Docker. The installer runs [Vaultwarden](https://github.com/dani-garcia/vaultwarden) 1.37.2 or later and publishes it on the tailnet only. |
| **broker** | Debian 12+ or Ubuntu 24.04+ with systemd 250+ and Python 3.11+. The installer adds the Bitwarden CLI, [fnox](https://fnox.jdx.dev), `jq`, `git`, `gpg` and `qrencode`. |
| **bellhop** (optional) | Debian 12+ or Ubuntu 24.04+ with systemd and Python 3.11+, plus the tools you want it to run. |
| **Agent machines** | macOS or Linux with Python 3.9+ and your agent, such as Claude Code or Codex. On macOS, [Homebrew](https://brew.sh) to install valetkey. |
| **Approver device** | A phone or computer no agent runs on, with a browser and an authenticator app (TOTP) for approving routes. For the terminal prompt instead of the browser: macOS or Linux with Python 3.9+. |

Sizes and details for each role are in the
[installation guide](docs/installation.md#requirements).

## Get started

Use a private Tailscale network. Run agents on dedicated machines. Keep them separate from the
broker, vault, bellhop, any machine where you enter keys or a service reads them, and the host that
administers those machines. An optional **trusted server** runs an application that needs a key
directly, such as a web app that reads its database password. Skip this role if all your tools use
brokered API routes. [Secure setup](docs/secure-setup.md)

1. **Install valetkey on each machine**, with the package manager of its system.

   **macOS: Homebrew.** One package per role.

   ```sh
   brew tap machinarii/valetkey https://github.com/machinarii/valetkey
   brew trust machinarii/valetkey
   brew install valetkey-agent      # machine where AI agents run; then: valetkey-agent-setup
   brew install valetkey-approver   # where you type keys
   valetkey-broker set-url https://broker.<tailnet>.ts.net
   ```

   **Linux: apt** (Debian and Ubuntu). One signed package for every role, including the three
   servers: the vault, the broker and the optional bellhop.

   ```sh
   curl -fsSL https://machinarii.github.io/valetkey/apt/valetkey.gpg | sudo tee /usr/share/keyrings/valetkey.gpg >/dev/null
   echo "deb [signed-by=/usr/share/keyrings/valetkey.gpg] https://machinarii.github.io/valetkey/apt ./" \
     | sudo tee /etc/apt/sources.list.d/valetkey.list
   sudo apt-get update && sudo apt-get install valetkey
   valetkey-setup                   # asks which machine this is, then sets it up
   ```

   On a Proxmox VE host, one command creates and installs the three servers; on another Linux
   distribution, run the installer script. Both are in
   [the installation guide](docs/installation.md#other-ways-to-install).

2. Follow the guide to sign the broker in, authorize your first route and verify it with a test
   credential. [Installation and first setup](docs/installation.md)

<a id="upgrading"></a>

## Updating valetkey

Run `valetkey-broker version` for the installed CLI version and `valetkey-broker update` for the latest public release.
On the broker, `sudo valetkey-broker update` installs the broker's own update.
The broker, vault, bellhop and clients have different update procedures.
[Update by role](docs/updating-valetkey.md)

## Guides

| I want to… | Read |
|---|---|
| Understand the broker, bellhop and network layout | [System design and containment](docs/system-design.md) |
| Add a credential, update a route or diagnose a failure | [Operations and command reference](docs/operations.md) · [Troubleshooting](docs/operations.md#troubleshooting) |
| Approve an API route with an authenticator, limit it in time or to some tools | [Route approval](docs/route-approval.md) |
| Use a service you sign in to (OAuth) | [Services you sign in to](docs/oauth-sign-in.md) |
| Use API credits with Claude Code or Codex | [Model API accounts and OpenRouter](docs/model-api-accounts.md) |
| Configure Claude Code, Codex or optional claude-mem capture | [Agent setup](docs/agent-setup.md) · Run `valetkey-broker guide claude-mem` for offline guidance |
| Run your agents from T3 Code | [T3 Code](docs/t3-code.md) |
| Add a tool, MCP server or Git remote | [New tool recipe](docs/recipe-new-tool-with-credential.md) · [Git through the broker](docs/git-through-the-broker.md) · [Several keys for one service](docs/several-keys-per-service.md) |
| Review the threat model or report a security issue | [Security model](docs/security-boundaries.md#security-model) · [Security policy](SECURITY.md) |
| See measured overhead or run the test suite | [Token and latency measurements](docs/performance.md) · [Development](docs/development.md) |

## Status

> [!IMPORTANT]
> **valetkey is currently intended for personal use, not for enterprise use.** It is a practical way to reduce the risk of running AI
> agents on your own machines, not a bulletproof one, and it has had no independent security audit.
> If you have compliance requirements, many users or keys whose loss you could not absorb, use it as
> a starting point and not as your only control.

Report vulnerabilities privately through GitHub's *Report a vulnerability*; see the
[security policy](SECURITY.md).

---

## License and credits

valetkey is released under the [MIT license](LICENSE). Copyright (c) 2026 Jinsoo An (machinarii).
The license requires that the copyright notice, with that name, stays in every copy or substantial
portion of the code. If you use valetkey or build on it, please also credit "Jinsoo An" with a link
to https://github.com/machinarii/valetkey; see [NOTICE](NOTICE).
