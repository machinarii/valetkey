# Security boundaries and limitations

## Security model

**Protects against:**

- a value at rest on any client machine: in files, `.env`, Keychain, shell history or logs;
- a client token leaking: there is none;
- a machine reading outside its routes, or one org's broker reading the other org;
- raw reads by an agent machine are denied by default, including direct HTTP requests. An owner-approved Claude setup-token route grants one machine its own model credential;
- typos and naming drift when keys are added: the agent names, you type only the value;
- losing the old key on a replace: a dated backup is always kept;
- writes from non-owner machines: only untagged, non-agent `owner_nodes` may submit; stored values are verified by hash.

**Does not protect against:**

- **Code on an allowed machine.** Every process shares its Tailscale identity. If trusted server `app-shop` may pull `*-shop-qa`, anything on `app-shop` can. Keep routes narrow and agents on separate machines or VMs.
- **A process that already holds a value** (`[project]` and `[value]` routes). It can leak that value. `[api]` routes avoid this.
- **Use of what is allowed.** A listed machine can call every operation its routes and bellhop entries permit, as often as it likes. Hiding a key stops copying, not misuse: narrow the lists, the key's own scope and its spending limits.
- **A bellhop tool's own behaviour.** The bellhop refuses subcommands that are not listed, but a listed subcommand does whatever the tool does. Do not list one whose purpose is to print the login or read a file you name.
- **Root on the broker**, which can unlock everything. Run nothing else on it, manage it only from its console or LAN SSH, keep it updated and back it up.
- **An agent on an approver device.** It could submit values, capture input or browser state, or read process memory. Keep approver devices separate.
- **Upstream key disclosure or misuse.** Echo/debug and secret-export APIs can return credentials. A route also permits the upstream actions it exposes. Narrow paths, methods and provider permissions. The broker masks the route's own key when a reply repeats it exactly or in base64; it is not a general response redactor.
- **Availability.** If the broker is down, clients get no keys. Keep what you need to repair it outside valetkey.

More in [SECURITY.md](../SECURITY.md).

### Should an agent machine hold its own model credential?

For a tool API, the agent can use a key without ever receiving it. The broker adds the key to an approved request. If the VM is compromised, someone can misuse the route, but removing the grant cuts off further calls through it. Could the broker do the same for Claude Code’s own subscription login?

Anthropic allows subscribers to sign in to the unmodified Claude Code client. Its policy prohibits developers from collecting users’ Claude.ai credentials or proxying subscription requests on their behalf. A gateway using a Console API key is an option for API billing. For subscription-only use, valetkey leaves model requests and authentication to Claude Code. Anthropic documents the native login flow without specifically endorsing a private vault handoff. [Claude Code credential policy](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use) · [Gateway billing](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways)

For unattended use, the owner can opt in to delivering one `claude setup-token` credential to one named VM. Claude Code then authenticates directly to Anthropic. The token lasts one year and authorizes model requests only. **Any process on that VM can fetch and copy it.** Removing the route stops new reads. A stolen copy can still work outside the tailnet until Anthropic revokes it or it expires. Owners who prefer native web login can use it without creating a valetkey route. Both choices apply only to Claude Code’s own login. [Setup-token scope](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token) · [Subscription setup](secure-setup.md#claude-code-subscription-sign-in)

**Claude Code is the only intended consumer of this grant.** It covers Claude’s own model credential on the named VM. If Codex or another agent shares that VM, it shares the machine’s ability to fetch the Claude token. Use separate VMs when those agents need separate access. For personal ChatGPT subscriptions, Codex uses its own ChatGPT sign-in. valetkey has no corresponding Codex token grant. OpenAI separately offers access tokens for eligible Enterprise workspaces. [Codex authentication](https://developers.openai.com/codex/auth/)

## Use at your own risk

valetkey is a personal project shared in the hope that it is useful. Read the following before you install or use it.

- **No warranty.** The software and its documentation are provided "as is", without warranty of any kind, express or implied, including fitness for a particular purpose and freedom from defects. It has had no independent security audit.
- **No liability.** To the fullest extent the law allows, the author and contributors are not liable for any claim, loss or damage arising from the software or its use. That includes leaked, stolen or misused credentials, charges run up on your accounts, lost or damaged data, downtime, and anything an AI agent does with the access you give it.
- **You are responsible for your own setup.** You decide where it runs, which machines and agents may use which keys and tools, and how narrow those keys are. Read the code and [secure setup](secure-setup.md) before you trust it with a real credential, start with test-mode or low-value keys, and keep your own backups of the vault.
- **Private use on a tailnet only.** It is meant for one person or a small trusted group running agents on their own machines. It is not designed, tested or supported for a public-facing service, for several tenants who do not trust each other, or for regulated, safety-critical or high-value uses. Never expose it to the internet.
- **It reduces risk; it does not remove it.** Nothing here guarantees that an agent cannot obtain, leak or misuse a credential. Do not make it your only control.
- **Other people's services and software.** You are responsible for following the terms of every service whose key or login you put behind the broker or the bellhop; some services restrict shared or automated use of an account. Vaultwarden, fnox, Tailscale, Claude Code and the other tools named here belong to their owners, come under their own licenses and terms, and are not covered by this notice. valetkey is not affiliated with or endorsed by any of them.
- **Not professional advice.** Nothing in this repository is security, legal or compliance advice.

The [MIT license](../LICENSE) is the governing text; this section explains it and does not replace it.

## Status and license

Version 0.5.1. In daily use on a small fleet of Proxmox, macOS and Linux machines with AI agents; the Proxmox scripts are the most tested path. bellhop is newer than the broker: it has run on one Proxmox VM with three tools, and its keyless tool standard is a draft that nothing enforces yet.

It is not a bulletproof solution for protecting keys from agents. It is a practical way to reduce risk in a home agentic setup, built by one person, without an independent security audit. Treat it as a reasonable set of precautions, not a guarantee.

Released under the [MIT license](../LICENSE), copyright Jinsoo An. The license requires that the copyright notice, with that name, stays in every copy or substantial portion of the code. If you use valetkey or build on it, please also credit "Jinsoo An" with a link to https://github.com/machinarii/valetkey in your README, documentation or about page; see [NOTICE](../NOTICE).
