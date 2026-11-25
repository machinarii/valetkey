# valetkey architecture

## Intent

Current policy: agents use broker API routes only. Raw consumers and owner entry run on separate
trusted machines. See [secure setup](secure-setup.md) for topology and migration requirements.

Keep every long-lived credential in Vaultwarden. Let the machines that use those credentials, and the AI agents on them, **use keys without storing them**, and never see a value inside an agent session. Exactly one machine, the broker, unlocks a vault. Everything else talks to broker services on it.

Two sides:

- **Pull and inject.** A machine fetches the values a process needs, at process start, into that process's memory only. Where the consumer calls an HTTPS API, the broker injects the key into the forwarded request, so the client never holds it.
- **Entry.** An agent that needs a credential not yet in the vault creates a *named request*. The owner types only the value, at a hidden terminal prompt or on a web page over Tailscale.

## Anti-goals (a failure even if it "works")

- A value printed in a terminal, log, chat or agent tool output, or written to any file on a client, including tmpfs.
- A client holding a vault password, `BW_SESSION` or a broker token.
- A machine reading an item outside its allowlist, or one org readable through the other org's broker.
- Agents choosing or typing values, or the owner typing names.

## Components

```text
 callers, over the tailnet            broker (Linux VM, TPM)
┌───────────────────────────┐        ┌──────────────────────────────────────────────┐
│ agent machine             │        │ broker-front :443 (Tailscale certificate)     │       ┌─────────────────────┐
│   valetkey-broker, API    │───────▶│   /local → broker-local  :8441 ─┐             │──────▶│ Vaultwarden         │
│   and MCP: never a key    │        │   /infra → broker-infra  :8442 ─┼─ read       │       │   local-org         │
├───────────────────────────┤        │   /entry → broker-entry  :8443 ─┘  and write  │       │   infra-org         │
│ trusted server            │───────▶│                                              │       └─────────────────────┘
│   fnox → vault shim       │        │ fnox proxy: adds the key to [api.*] calls    │       ┌─────────────────────┐
├───────────────────────────┤        │ broker-approval: authenticator, route grants  │──────▶│ service APIs and    │
│ approver device           │───────▶│ broker-entry: key entry; checks what a stored │       │ MCP servers         │
│   valetkey-entry, browser │        │   key opens on its MCP routes                │       └─────────────────────┘
└───────────────────────────┘        └──────────────────────────────────────────────┘
```

| Piece | File | Runs as | Notes |
|---|---|---|---|
| Front door | `broker/broker_front.py` | `broker-front` | TLS with the Tailscale certificate (`broker-cert`, renewed weekly). Strips the `/local`, `/infra` or `/entry` prefix and sets `X-Broker-Peer` to the TCP peer. Answers loopback-only paths with 404. Has no vault access. |
| Read service | `broker/broker_read.py --org local\|infra` | `broker-local`, `broker-infra` | Started by `with-bw-session`, which unlocks with the `systemd-creds` credential, writes `BW_SESSION` to a 600 file under `/run`, and wraps the service in `fnox proxy run` when the org has `[api.*]` routes. The service's own environment never contains `BW_SESSION`. |
| Entry service | `broker/broker_entry.py` | `broker-entry` | Holds no session. Unlocks → writes → verifies → locks per owner submission. Requests are kept in `/var/lib/broker-entry/requests.json` (names, never values) and expire after 24 h. After a value is stored, it checks the routes that use it (`brokerlib/access_check.py`): for an MCP server it signs in with the new key at that route's own upstream and lists the tools, never running one. |
| Approval worker | `broker/approval_worker.py` | `broker-approval` | Pairs the owner's authenticator (console only) and approves proposed `[api.*]` routes with its codes. Reached by the entry service over a local socket. |
| Shared libs | `broker/brokerlib/` | | `bwclient` (the only module that runs `bw`; 5-minute item cache), `routes` (parse and validate), `identity` (`tailscale whois`, cached 5 min), `requests_store`, `httpbase`. |
| Broker CLIs | `broker/bin/` | root | `valetkey-broker login`, `valetkey-broker check`, `valetkey-broker routes`, `valetkey-broker update`, `broker-cert`, `with-bw-session`. |
| Clients | `client/` | user | `valetkey-broker` (check/request/status/backups), `vault` (fnox shim), `valetkey-entry` (owner). Stdlib Python ≥ 3.9; no proxies from the environment; redirects are errors. |

## Identity

Caller = the first DNS label of `tailscale whois --json <peer-ip>`, lowercased and cached for 5 minutes. The label counts only when the rest of the name is the broker's own tailnet suffix (`tailscale status --json`, `Self.DNSName`): a device from another tailnet (node sharing) chooses its own short name, so it is an unknown caller. An unknown peer gets 403. Tagged nodes are never approver devices. Backends trust `X-Broker-Peer` only from 127.0.0.1, so a client cannot forge it through the front door.

Identity uses `Node.Name`, not the display `ComputedName`. Trust the tailnet administrators and
prevent renamed or newly enrolled devices from inheriting stale short-name grants; stable node
IDs are not pinned. `[security].agent_nodes` cannot overlap raw-route or owner roles. Raw reads
also require `[security].trusted_value_nodes`, empty by default, in addition to route permission.

## Endpoints

Read service (under `/local` or `/infra`):

| Method and path | Who | Returns |
|---|---|---|
| `GET /value/<name>` | `[value.<name>]` nodes | raw value |
| `GET /<project>/value/<name>[/notes]` | `[project.<project>]` nodes, item matches `items` | raw value |
| `GET /check?names=a,b/notes` | any known machine | `{"a":"ok"\|"missing"\|"not-allowed"}` |
| `GET /backups/<name>` | machines that may read `<name>` | `{"backups":["YYYYMMDD",…]}`, newest first |
| `* /<route>/<path>` | `[api.<route>]` nodes | the upstream's response; the key is added by `fnox proxy` |
| `POST /refresh`, `GET /names` | loopback secret only | for `broker-entry` |

A backup `<name>-backup-YYYYMMDD` requires the original item's raw-route permission and trusted
consumer policy. API-only callers can check their exact API item reference but cannot read it
or fetch backups. `ok` reports presence/access, not upstream authentication. Value reads are
rate-limited to 300 per minute per machine.

Entry (under `/entry`):

| Method and path | Who |
|---|---|
| `POST /v1/requests`, `GET /v1/requests/<id>` | any known machine (at most 20 pending requests per machine, then 429) |
| `GET /v1/requests?status=pending`, `POST /v1/requests/<id>/value`, `POST /v1/replace` | approver devices (`entry.toml` `owner_nodes`) |
| `GET /`, `POST /submit`, `POST /replace` | approver devices: the web page |
| `POST /v1/suspect`, `POST /v1/unsuspect` | loopback secret: read services mark items that got upstream 401/403 |

## Writes

For each owner submission:

1. Re-validate the name and org under a lock. A second submission of the same request is refused, so a double tap can't overwrite a backup.
2. If the request is a replace and the item exists, rename it to `<name>-backup-YYYYMMDD`. If that backup already exists, refuse before mutation. Renaming keeps the old value without it ever passing through a response.
3. Create the item in the org's collection. For an `infra` request with a project, that is the nested collection `<collection>/<project>`, which must already exist. Single-line values go in Password, multi-line values in Notes, and the purpose goes in the notes.
4. Read the item back and compare SHA-256 hashes in the process. On a mismatch or error, delete the new item and undo the rename.
5. Mark the request `stored` or `replaced`, ask the matching read service to refresh, and write an audit line (id, name, org, outcome, caller).

Names follow `^[a-z0-9]+(-[a-z0-9]+)*-(dev|qa|prod)$` with a project, or `^[a-z0-9]+(-[a-z0-9]+)*$` without one, and may never end in `-backup-YYYYMMDD`. A request for a name that is one `-segment` away from an existing item is flagged *near duplicate*, and the owner sees the flag before entering.

## Files on the broker

| Path | Content | Mode |
|---|---|---|
| `/etc/broker/routes.toml`, `entry.toml`, `vault.env` | names, URLs | 644 |
| `/etc/credstore.encrypted/bw-unlock-{local,infra,entry}` | unlock passwords, `systemd-creds` (TPM + host key) | 600 |
| `/etc/credstore.encrypted/broker-loopback` | shared loopback secret | 600 |
| `/var/lib/broker-<name>/` | `bw` data dir, `org.json` (org and collection IDs), `api.toml` (fnox references only) | 700 |
| `/var/lib/broker/tls/` | Tailscale certificate | 750, group `broker-front` |
| `/run/broker-<org>/bw.env` | `BW_SESSION` of the running read service | 600, tmpfs |

## fnox integration

fnox 1.35's `vault` provider shells out to a `vault` CLI rather than speaking HTTP (see [fnox-vault-provider.md](fnox-vault-provider.md)). The `client/vault` shim answers exactly `vault kv get -field=<field> <path>/<name>` with one GET to `$VAULT_ADDR/<path>/value/<name>[/<field>]`. `VAULT_TOKEN` is ignored. A missing or forbidden item prints `No value found at …` and exits 2, which fnox reports normally. With `if_missing = "error"`, `fnox exec` stops instead of starting the app with an empty variable.

On the broker, `valetkey-broker routes install` turns each org's `[api.*]` routes into an fnox config (`gen-fnox-config.py`) of `[[proxy.rules]]` with exact host, methods and paths, and bitwarden-provider references. `fnox proxy run` then injects values into matching requests. fnox resolves every proxy secret before it starts the broker, so a single `[api.*]` item it cannot find (missing, or a name another item's name contains) stops that org's broker; `valetkey-broker routes install` checks the items first, waits until the broker listens, and puts the previous config back if it never does. [Git through the broker](git-through-the-broker.md) is a worked example of such a route.
