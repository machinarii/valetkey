<p align="center">
  <img src="docs/images/valetkey-icon.png" width="96" height="96" alt="Valetkey">
</p>

# valetkey

## Your machines get a valet key, never the master.

Give an AI agent access to a tool while keeping the tool’s credentials on a separate machine. **Valetkey is designed for Claude Code with a Claude subscription and Codex with a ChatGPT subscription.** Credit-based model API accounts are outside this guide’s scope. The tools your agent calls can still use API keys. Memory capture with **claude-mem is optional**.

## How it works

- For an **API**, the agent calls a route on the broker. The broker checks the calling machine, HTTP method and path, then adds the key from Vaultwarden and forwards the request.
- For a **command-line tool with its own login**, the optional desk runs approved commands and returns their results.
- When an agent needs a new key, it requests the item by name. You enter the key on your **approver device**, a phone or computer where you can also approve requested routes. Keep agents off this device. Use the broker’s entry page in your browser or a hidden prompt in your terminal.

![An agent calling a broker API route and running a tool on the desk](docs/images/how-it-works.gif)

**Claude’s own model login is a deliberate exception.** For unattended subscription use, the owner can grant one VM its own Claude setup-token. Any process on that VM can copy it, and removing the grant cannot recall a copy. Native Claude web login is also available. Codex uses its own ChatGPT sign-in and has no corresponding Valetkey grant. [Why the subscription terms shaped this choice](docs/security-boundaries.md#should-an-agent-machine-hold-its-own-model-credential)

## Installation

Use a private Tailscale network. Run agents on dedicated machines. Keep them separate from the broker, vault, desk, any machine where you enter keys or a service reads them, and the host that administers those machines. An optional **trusted server** runs an application that needs a key directly, such as a web app that reads its database password. Skip this role if all your tools use brokered API routes. An agent can still misuse an approved operation. This project has had no independent security audit. [Secure setup](docs/secure-setup.md) · [Security limits](docs/security-boundaries.md)

1. Prepare your tailnet and decide where each role will run. [Requirements and machine roles](docs/installation.md#requirements)
2. Run the installer on each machine and select its role. Linux and macOS share the same installer. [Installation guide and Homebrew option](docs/installation.md)

   ```sh
   git clone https://github.com/machinarii/valetkey.git
   cd valetkey
   ./install.sh
   ```

   On a fresh Proxmox VE host, `sudo ./install.sh proxmox` creates the infrastructure guests and installs their services. [Proxmox setup](docs/installation.md#fresh-installation-on-proxmox-ve)

3. Follow the guide to sign the broker in, authorize your first route and verify it with a test credential. [First setup and verification](docs/installation.md#getting-started)

<a id="upgrading"></a>

## Updating valetkey

Run `relay version` for the installed CLI version and `relay latest-version` for the latest public release. The broker, vault, desk and clients have different update procedures. [Update by role](docs/updating-valetkey.md)

## Guides

| I want to… | Read |
|---|---|
| Understand the broker, desk and network layout | [System design and containment](docs/system-design.md) |
| Add a credential, update a route or diagnose a failure | [Operations and command reference](docs/operations.md) · [Troubleshooting](docs/operations.md#troubleshooting) |
| Configure Claude Code, Codex or optional claude-mem capture | [Agent setup](docs/agent-setup.md) · Run `relay guide claude-mem` for offline guidance |
| Add a tool, MCP server or Git remote | [New tool recipe](docs/recipe-new-tool-with-credential.md) · [Git through the broker](docs/git-through-the-broker.md) |
| Review the threat model or report a security issue | [Security model](docs/security-boundaries.md#security-model) · [Security policy](SECURITY.md) |
| See measured overhead or run the test suite | [Token and latency measurements](docs/performance.md) · [Development](docs/development.md) |

[MIT license](LICENSE) · [Attribution](NOTICE) · [Use at your own risk](docs/security-boundaries.md#use-at-your-own-risk)
