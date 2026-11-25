# Set up Valet so agents cannot read keys

Keep the broker, vault and credential-bearing processes off machines where agents run. The
broker-only model depends on that separation. A hidden prompt, a rule in `AGENTS.md`, a different
Unix user or an ordinary Docker container on the agent's host does not provide the same boundary.

## Choose the machines first

| Role | May hold credentials | May run agents | Allowed interfaces |
|---|---|---|---|
| Agent VM | Owner-approved Claude subscription credential (setup token or native web session) | Yes | broker HTTPS API routes, checks, named requests and the optional setup-token route |
| broker VM | Unlock material, vault sessions and injected keys | No | Agent-facing HTTPS 443; backend ports on loopback only |
| bellhop VM (optional) | The logins of command-line tools it runs for agents | No | Agent-facing HTTPS 443; the bellhop service on loopback only |
| vault server | Encrypted vault database and server secrets | No | HTTPS from broker and separate approver devices |
| Approver device | Owner-entered values during private entry | No | vault UI, broker entry UI or `valetkey-entry` |
| Trusted server (optional) | Only the keys its service requires | No | Narrow application interface; raw-value routes if unavoidable |
| Hypervisor / management host | Administrative access to the above | No | Owner-only management; no agent access to VM disks or consoles |

In broker configuration, list approver devices under `owner_nodes` in `entry.toml`.
List trusted servers under `security.trusted_value_nodes` in `routes.toml` and grant
each server a route for the specific credential it needs.

Separate physical hosts provide a stronger boundary. Dedicated VMs on a trusted hypervisor can
be used when the agent has no host/root access, management socket, shared secret volume, clipboard,
SSH forwarding, or access to the other VMs' disks. Do not host the broker VM under a desktop
account controlled by an agent. A container is especially insufficient if an agent can use the
Docker socket, mount host paths, or share the host's Tailscale identity.

Keep agents off the **approver device** too. Hidden input prevents terminal echo; it cannot
protect against code on that device capturing keystrokes, browser fields or process memory.
Never use a recorded agent terminal or shared agent browser for entry.

## Model API accounts

Claude Code and Codex can use API credits through a broker route, including OpenRouter.
Choose this per launcher and keep the provider key in Vaultwarden. [Configure an API account](model-api-accounts.md)

## Claude Code subscription sign-in

Choose a method per machine. `valetkey-broker guide claude` shows both choices offline.

### Web login

On a machine approved to hold a local Claude session, the owner runs `claude auth login`
using the native Claude Code executable, as the same OS user and with the same Claude
configuration directory as T3. Select the Claude subscription in the browser. Claude Code
manages the local session. In T3, select that executable and use a launch environment
without a setup-token or API credential override. Verify with a short model turn. This
method requires no broker route. An existing setup-token route remains active until the
owner removes it separately. [Claude Code authentication](https://code.claude.com/docs/en/authentication)

### OAuth token from `claude setup-token`

For a new token, the owner runs `claude setup-token` on a separate approver device in an
unrecorded terminal, approves in the browser, then enters the token privately through the
requested broker entry page or `valetkey-entry`. For an existing token, keep its Vaultwarden
item. In T3, select the owner-approved launcher that supplies `CLAUDE_CODE_OAUTH_TOKEN`
to the Claude process on each launch.

For an owner-approved launcher that reads its setup token from valetkey, keep the machine
in `agent_nodes`. Any process on that machine can copy the token and use the subscription.
In the broker's `/etc/valetkey-broker/routes.toml`, configure the value route as shown. If the
table already exists, edit it in place.

```toml
[value.claude-code-oauth-agent-dev]
org = "local"
item = "claude-code-oauth-agent-dev"
nodes = ["agent-dev"]
claude_setup_token = true
```

The broker accepts one agent per flagged route and requires the route and item to match
that agent's name exactly. The grant covers the current password field only. Backups,
notes, other nodes and the infra organization are outside this grant.

The broker updater validates the complete routes file before installing. Resolve any other
agent/value or agent/project conflicts through the existing consumer migration procedure
before rollout. Once the updated broker is installed, apply a route edit with
`sudo valetkey-broker routes check && sudo valetkey-broker routes install local`. On the agent VM,
`valetkey-broker check local claude-code-oauth-agent-dev` should return `ok`. That verifies storage
and permission. Verify subscription authentication separately with a short T3 model turn.

## Network and identity

Use [the ACL example](../deploy/tailscale/acl.example.hujson) as a starting point, substituting
the actual machine addresses privately in the owner-managed policy. Allow agents to reach only
the broker's TCP 443. Deny access to the vault, broker SSH/backend ports, approver devices, trusted
consumer administration, and the hypervisor. A trusted server may expose a narrow application
endpoint to agents; do not expose arbitrary command execution or unreviewed deployment.

Remove broad member-to-member and allow-all rules when merging the example: Tailscale grants are
additive. A `deny` assertion in the example's tests is a test, not a firewall rule. Review LAN,
IPv6, subnet-router and public interfaces too; the tailnet policy cannot close another network
path. Keep backend ports 8441–8443 bound to loopback and the front door on the tailnet address.

Limit tailnet administration, enrollment and device renaming to trusted operators. Valet maps
the peer's Tailscale DNS name to its first label; it does not pin a stable node ID. Never grant
untrusted/shared-in devices reachability to the broker based on a matching short name, and
remove stale routes/owner names before reusing a name. Cached identity can lag changes by up to
five minutes. Tagged service nodes cannot submit as owners, even if their name is listed.

TLS certificate verification stays enabled. Do not use `curl -k`, custom interception proxies,
or HTTP for real entry. Clients ignore proxy environment variables and refuse redirects. The
HTTP loopback exception in client code exists for local fixtures only.

## broker policy: fail closed

Place names, never values, in `/etc/valetkey-broker/routes.toml` on the separate broker:

```toml
[security]
agent_nodes = ["agent-dev"]
trusted_value_nodes = ["app-prod"]

[api.payments-status]
org = "infra"
item = "payments-api-key-shop-prod"
upstream = "https://api.stripe.com"
auth = "Bearer {}"
methods = ["GET"]
paths = ["/v1/balance"]
nodes = ["agent-dev"]

[value.database-url-shop-prod]
org = "infra"
item = "database-url-shop-prod"
nodes = ["app-prod"]
```

Ordinary raw reads require membership in `trusted_value_nodes` and a matching value or
project route. The Claude subscription exception above grants one item to one agent through
an explicit value route. Keep owner nodes and trusted servers on separate machines.

Use exact item names over suffix wildcards. Split development and production routes. An API
prefix without a trailing slash permits that endpoint and its children, not similarly named
siblings; `/v1/balance` permits `/v1/balance` and `/v1/balance/...`, not `/v1/balance-admin`.
Upstreams must be HTTPS origins on port 443; embedded userinfo, upstream URL paths and query
strings are rejected. Ambiguous paths, including encoded traversal and separators, are denied.

API-only access still grants the actions the upstream key permits. Use a narrowly scoped,
restricted provider key and read-only methods wherever possible. Exclude echo/debug/header
reflection, token export, secret management and arbitrary URL forwarding endpoints. Do not
allow an API that returns the injected key. Valet streams upstream responses; it cannot prove
that an arbitrary upstream will never disclose a credential or permit a destructive action.
Method/path checks do not validate the meaning of query parameters or request bodies.

Raw consumers must run reviewed code and dependencies. Allowing an agent to change a consumer's
startup script, deploy arbitrary code, run a shell, or read its environment defeats separation.
For non-HTTP protocols, build a narrow trusted service or retain a separate consumer; do not
give an agent the key because an API proxy is inconvenient.

## Install without widening roles

Use reviewed source pinned to a release or commit. Avoid piping a moving remote script into a
privileged shell. `sudo valetkey-broker update` on the broker console fetches the repository (over
https when it is public, with a read-only deploy key when it is private) and installs only after
you type `y` to the commit it shows; review the linked diff first, because everyone who can
push chooses what that commit contains. Do not wrap it in a timer, a webhook or anything an agent
can reach. Run broker installation and login only from an owner's private management terminal on
the separate broker. Use view-only vault accounts per read org, collection-scoped
access, and a separate write account. Keep the owner account protected with MFA.

On an agent machine, the client command is:

```sh
./install.sh client --agent --broker-url https://broker.<tailnet>.ts.net
valetkey-broker guide
```

`--agent` installs the metadata CLI and its guidance; it does not install the raw-value shim or
owner-entry tool. It does not remove an older installation, erase pre-existing secrets, or enforce
access by itself. broker policy must be installed by the trusted operator. Never copy the vault's
login state, broker unlock material, `BW_SESSION` or raw-value runtime mounts onto an agent VM.

On a separate approver device, install the client with `--owner`. Enter new credentials either in
that device's private terminal using `valetkey-entry <request-id>` or in the encrypted Tailscale
entry page. Both options remain available; never ask the owner to paste a value into chat.
Piped owner input is refused, and failure to disable echo aborts password entry.

`valetkey-broker guide` works offline. The installed file is
`~/.local/share/valetkey/credential-rules.md` with the default prefix. Put a reference to it in
each agent's persistent instructions. `./install.sh guidance` prints the same file from a
checkout. Neither command reads a credential or installs instructions into unrelated projects.

## Migrate an existing deployment without breaking its consumers

Ordinary raw routes return 403 when `trusted_value_nodes` is empty. Do not
deploy the new broker code blindly onto a running raw-value setup.

1. Inventory **references and consuming services only**, from approved metadata. Identify which
   machines run agents and which services currently depend on raw-value routes. Do not dump keys.
2. Provision separate trusted servers and reviewed API routes alongside the old working paths.
   Copy neither secrets nor unlock material to agent machines. Existing exposed credentials
   require owner-led rotation; this change cannot erase old logs, disks or process memory.
3. Prepare `agent_nodes`, `trusted_value_nodes`, narrow routes and separate owner nodes together.
   Validate the candidate configuration with the new parser in a safe staging environment.
4. Test the new consumers and route behavior with synthetic credentials first. The operator then
   verifies the actual service's storage and authentication status without disclosing values.
5. In the operator's change window, install the reviewed broker code and policy. Run
   `sudo valetkey-broker routes check`, `sudo valetkey-broker routes install`, and restart `valetkey-broker-entry` so it loads
   the new policy. Both read services and the entry service must use the same policy revision.
6. Remove obsolete access only after the replacement works. Do not temporarily add an agent
   machine to `trusted_value_nodes` as a workaround. Maintain a recovery path through the
   trusted management console, with encrypted backups and credentials kept outside agent reach.

Do not run these owner administration commands from a cloud agent session. Source tests in
this repository do not establish that a live broker has been updated or correctly configured.

## Verify without showing a value

| Check | Expected result | What it proves |
|---|---|---|
| `valetkey-broker check infra payments-api-key-shop-prod` on an authorized agent | `ok` or `missing` | API item is authorized and present/absent; not upstream authentication |
| `valetkey-broker check infra database-url-shop-prod` on that agent | `not-allowed` | Raw-only reference is not usable by the agent |
| Harmless approved API operation | Expected provider status, no credentials in output | Authentication and narrowly allowed behavior work |
| broker policy validation | No role conflicts; only intended trusted servers | Names and roles are coherent |
| Synthetic direct raw/notes/backup requests | 403, no fixture value | HTTP callers cannot bypass the broker policy |
| Synthetic unauthorized write, spoofed peer and traversal tests | Denied | The tested authorization boundaries hold |

Run `make test-security` on Linux for the synthetic tests in a disposable sandbox: no external
network, host home or live services; read-only source/toolchain; bounded temporary storage and
CPU, process, memory, descriptor and wall-clock limits. Missing sandbox support fails closed.
Use `make test` only in an already isolated test environment. Do not test a denial against a
real raw-value endpoint from an agent: a misconfiguration could return the actual secret.

Protect broker/consumer memory as well as files: restrict debugging and admin access, disable
core dumps for credential-bearing services, and use disabled or encrypted swap and encrypted
snapshots/backups. Tmpfs is not an absolute no-disk guarantee on a system with unencrypted swap.
Avoid shell tracing, verbose HTTP dumps and SDK debug logs. Audit names, action outcomes and
policy changes; never log request/response bodies containing credentials.

If a credential might have leaked, preserve only non-secret evidence, isolate the affected
machine, and have the owner rotate/revoke it in Vaultwarden and the upstream service. Do not
have an agent print the value to determine whether it is the same one. Protect dated backups
as credentials too; repeated same-day replacement is refused rather than silently deleting one.
