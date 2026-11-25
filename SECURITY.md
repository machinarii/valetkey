# Security

## Reporting

Please report vulnerabilities privately through GitHub's *Report a vulnerability* (Security tab) rather than in a public issue. Include the version (`./install.sh version`), what you observed and how to reproduce it. Never include real credential values.

## Threat model

valetkey is not a bulletproof solution for protecting keys from agents. It is a practical way to
reduce risk in a home agentic setup, and it has had no independent security audit.

See [secure setup](docs/secure-setup.md) for required machine separation, policy, migration and
verification, and [the threat model](docs/valetkey-threat-model.md) for evidence and limits.

Agents use broker-only API access by default. Ordinary raw reads require both a matching
route and membership in `trusted_value_nodes`. An explicit `claude_setup_token = true`
route lets every process on the named agent VM read its current Claude subscription token.
See [Claude Code subscription sign-in](docs/secure-setup.md#claude-code-subscription-sign-in).

**Assets:** credential values in Vaultwarden; the three broker unlock passwords on the broker.

**Trusted:** the broker's root user and hypervisor; Tailscale identity, enrollment/renaming administrators and certificates; Vaultwarden; separate approver devices; separate credential-bearing consumers and their reviewed code; authorized upstream APIs that do not return credentials.

**Untrusted:** every other tailnet machine beyond what its routes allow; AI agents and other code on client machines; the LAN; the internet.

| Threat | Control |
|---|---|
| An agent client's disk, backups or dotfiles leak | broker API use does not deliver provider keys. This cannot clean up credentials from an older installation. |
| A stolen broker client token | Valet issues no client bearer token. Tailscale machine identity remains security-sensitive. |
| A machine reads beyond its scope | Every route lists `nodes`; projects list `items`. Unlisted gets `not-allowed`, unknown gets 403, and there is no list endpoint. |
| One org read through the other | Separate Linux users, `bw` data dirs, accounts and unlocks per org. A read account is a member of one org only. |
| An agent directly fetches a key | Raw reads require explicit trusted server policy plus route authorization. API-only nodes can check presence without receiving values. |
| An agent abuses an API route | Keys are injected by `fnox proxy` for a fixed HTTPS host, allowed methods and canonical path boundaries. Caller auth/cookies and common URL/method override headers are dropped. Upstream semantics and responses remain trusted. |
| Spoofed caller identity | The front door overwrites `X-Broker-Peer` with the TCP peer, and backends trust it only from loopback. Loopback-only paths are 404 at the front door. |
| Values leaking through logs or errors | Audit lines carry names and outcomes only. Errors never echo response bodies or `bw` output. Tests check this. |
| Unauthorized writes | Only untagged, non-agent `owner_nodes` may submit. The write account is unlocked per write. Replace preserves dated backups, including same-day collisions. Stored values are verified by SHA-256. |
| Owner input echoes or travels over plaintext | Owner CLI refuses piped input and password-echo fallback; rejects remote HTTP, userinfo URLs, proxies and redirects; never prints submission response bodies. |
| Entry-page script injection | Values/descriptions are HTML-escaped, forms use a token, and a CSP admits only the bundled script/style hashes, same-origin forms and no framing. |
| A brute-force or runaway client | 300 value reads per minute per machine. |
| broker compromise through services | Hardened units: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, restricted address families, and write access only to the unit's own state dir. |

## Known limits

- **Same-machine code shares identity.** Never run agents on the broker, vault server, approver devices, raw-key consumers or their management host. Keep VMs isolated from host management, shared secret volumes and forwarding sockets. A different Unix user or ordinary container on an agent host is not this guarantee.
- **Pulled values are in process memory.** A `[project]` or `[value]` consumer can leak what it holds. Prefer `[api]` routes.
- **Approver devices hold entry values.** An agent there could submit requests, capture input or read process/browser memory. Hidden prompts prevent echo, not a local compromise.
- **Names are not stable identity pins.** Short DNS labels are used for policy, with a five-minute cache. Restrict node enrollment/renaming and shared-in access; remove stale grants before reusing names. Stable node-ID pinning is not implemented.
- **Sign-in (OAuth) routes do not go through fnox.** The read service adds the current token itself and connects straight to the service; valetkey-broker-entry fetches the service's sign-in documents, registers a client and renews the token. Those fetches go only to public `https` addresses on port 443 that the broker resolved and checked itself, never to tailnet or private addresses. The access and refresh tokens live in the vault item; a stolen refresh token lasts until the service withdraws it. See [Services you sign in to](docs/oauth-sign-in.md).
- **API responses are streamed, with only the route's own key masked.** The broker replaces the key it injected (as stored, in base64, or as a Basic credential) with `*` in the reply's headers and body, and writes `key hidden in reply` in the audit line. A reply that re-encodes the key (escapes, compression the broker did not ask for) or returns a different secret passes. Exclude reflection/debug/token-export endpoints and unrestricted URL forwarding. API permissions can also be abused without stealing a key.
- **Host storage policy matters.** Process memory/tmpfs may reach swap, dumps or VM snapshots. Protect these separately; source tests do not validate the deployed host.
- **Root on the broker sees everything.** Run nothing else there. Manage it only from its console or LAN SSH (the example ACL blocks tailnet SSH), keep it patched and back it up.
- **Availability.** If the broker or Vaultwarden is down, clients get no keys. Keep recovery credentials (hypervisor login, broker console access) outside valetkey.

## Operational rules

- Values are entered only at hidden prompts (`valetkey-broker login`, `valetkey-entry`) or in Vaultwarden or the entry page. They never go in command arguments, files in this repo, chat, or cloud documents.
- Read accounts are *Can view*, never *Admin* or *Owner*. Collection access must not be *hide passwords*, or every item reports `FAIL`.
- Back up Vaultwarden's data directory and the broker VM. The vault items are encrypted, but the backups still need protecting.
- Old backups (`<name>-backup-YYYYMMDD`) are never deleted automatically. Remove them yourself after rotating.
- `valetkey-broker guide` and `./install.sh guidance` provide offline memory rules. `--agent` installs only
  metadata tools; server policy supplies enforcement. Existing tools or secrets are not removed.
- Report storage/access (`valetkey-broker check`) separately from upstream authentication. Test denied raw
  endpoints only with synthetic credentials; a live misconfiguration could return a real key.
