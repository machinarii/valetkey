<p align="center">
  <img src="docs/images/valetkey-icon.png" width="128" height="128" alt="valetkey icon: a smiling robot valet in a green cap holds up a gold key, on a blue tile">
</p>

<h1 align="center">valetkey</h1>

<h2 align="center">Your agents use the key, but they never see it.</h2>

<p align="center">
  Give an AI agent access to a tool while the credentials stay on a separate machine:<br>
  a credential broker and a tool desk on their own machines, joined over your private Tailscale network.
</p>

<p align="center">
  <img alt="Python: standard library only" src="https://img.shields.io/badge/python-standard%20library%20only-3776ab?style=flat-square&logo=python&logoColor=white">
  <img alt="Self-hosted with Proxmox, Tailscale and Vaultwarden" src="https://img.shields.io/badge/self--hosted-Proxmox%20%C2%B7%20Tailscale%20%C2%B7%20Vaultwarden-1d4e6e?style=flat-square">
  <img alt="Clients: Linux and macOS" src="https://img.shields.io/badge/clients-Linux%20%C2%B7%20macOS-2d81ff?style=flat-square">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-3a3a3a?style=flat-square">
  <img alt="Status: 0.2.2, for home setups" src="https://img.shields.io/badge/status-0.2.2%20%C2%B7%20for%20home%20setups-d69e2e?style=flat-square">
</p>

<p align="center">
  <a href="#get-started"><b>Install valetkey</b></a> ·
  <a href="docs/secure-setup.md">Secure setup</a> ·
  <a href="#guides">Guides</a>
</p>

<br>

<p align="center">
  <img src="docs/images/how-it-works.gif" width="860" alt="Animation in eleven steps, with a legend: a keyhole is a request with no key in it, a gold key is a master key (the real credential), a silver key with one tooth is a valet key (which routes a machine may use, kept on the broker), a white card is a tool's own login. An agent machine that holds no keys sends the broker a request with an empty keyhole. The broker checks which machine is calling and that its valet key fits the route, takes that route's master key from the vault, puts it in the keyhole and forwards the request to the service. The answer comes back without the master key. For a tool with its own login, the agent calls the desk, which runs the real tool with that login. A command that would print the login is refused.">
  <br>
  <sub>Agents send keyholes, never keys. The broker puts the master key in a request on its way out; the desk runs tools that keep their own login.</sub>
</p>

> [!WARNING]
> **For a private tailnet only, and at your own risk.** Never expose the broker, desk or vault to
> the internet. valetkey reduces the risk of agents reading your keys; it does not stop an agent
> misusing an operation you approved, and it has had no independent security audit.
> [Security limits](docs/security-boundaries.md) · [Use at your own risk](docs/security-boundaries.md#use-at-your-own-risk)

<p align="center">
  <a href="#how-it-works">How it works</a> ·
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
      <h3>🛎️ A desk for tools that sign in</h3>
      Command-line tools that keep their own login run on a separate desk machine. Agents use them by name and get the output, never the login.
    </td>
    <td width="25%" valign="top">
      <h3>🧱 Containment first</h3>
      Every part has its own machine and one installer. Agents run apart from the broker, vault, desk and the devices where you enter keys.
    </td>
    <td width="25%" valign="top">
      <h3>🙋 You stay in charge</h3>
      Agents ask for keys and routes by name. You type the value, or approve a route with an authenticator code, on your own approver device.
    </td>
  </tr>
</table>

<br>

## How it works

1. **APIs:** the agent calls a route on the broker. The broker checks the calling machine, HTTP
   method and path, then adds the key from Vaultwarden and forwards the request.
2. **Tools with their own login:** the optional desk runs approved commands and returns their
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

## Get started

Use a private Tailscale network. Run agents on dedicated machines. Keep them separate from the
broker, vault, desk, any machine where you enter keys or a service reads them, and the host that
administers those machines. An optional **trusted server** runs an application that needs a key
directly, such as a web app that reads its database password. Skip this role if all your tools use
brokered API routes. [Secure setup](docs/secure-setup.md)

1. **Your devices** (macOS or Linux): install with Homebrew, one package per role.

   ```sh
   brew tap machinarii/valetkey https://github.com/machinarii/valetkey
   brew trust machinarii/valetkey
   brew install valetkey-agent      # machine where AI agents run; then: valetkey-agent-setup
   brew install valetkey-approver   # where you type keys
   relay set-url https://broker.<tailnet>.ts.net
   ```

2. **The servers** (vault, broker, optional desk; Linux): on a Proxmox VE host,
   `sudo ./install.sh proxmox` creates and installs all three. Otherwise run the installer on each
   server and choose its role:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash
   ```

3. Follow the guide to sign the broker in, authorize your first route and verify it with a test
   credential. [Installation and first setup](docs/installation.md)

<a id="upgrading"></a>

## Updating valetkey

Run `relay version` for the installed CLI version and `relay update` for the latest public release.
The broker, vault, desk and clients have different update procedures.
[Update by role](docs/updating-valetkey.md)

## Guides

| I want to… | Read |
|---|---|
| Understand the broker, desk and network layout | [System design and containment](docs/system-design.md) |
| Add a credential, update a route or diagnose a failure | [Operations and command reference](docs/operations.md) · [Troubleshooting](docs/operations.md#troubleshooting) |
| Approve an API route with an authenticator | [Route approval](docs/route-approval.md) |
| Use API credits with Claude Code or Codex | [Model API accounts and OpenRouter](docs/model-api-accounts.md) |
| Configure Claude Code, Codex or optional claude-mem capture | [Agent setup](docs/agent-setup.md) · Run `relay guide claude-mem` for offline guidance |
| Add a tool, MCP server or Git remote | [New tool recipe](docs/recipe-new-tool-with-credential.md) · [Git through the broker](docs/git-through-the-broker.md) · [Several keys for one service](docs/several-keys-per-service.md) |
| Review the threat model or report a security issue | [Security model](docs/security-boundaries.md#security-model) · [Security policy](SECURITY.md) |
| See measured overhead or run the test suite | [Token and latency measurements](docs/performance.md) · [Development](docs/development.md) |

## Status

> [!IMPORTANT]
> **valetkey is for home setups.** It is a practical way to reduce the risk of running AI
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
