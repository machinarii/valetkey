# valetkey architecture

## Intent

Current policy: agents use broker API routes only. Raw consumers and owner entry run on separate
trusted machines. See [secure setup](secure-setup.md) for topology and migration requirements.

Keep every long-lived credential in Vaultwarden. Let the machines that use those credentials, and the AI agents on them, **use keys without storing them**, and never see a value inside an agent session. Exactly one machine, the broker, unlocks a vault. Everything else talks to relays on it.

Two sides:

- **Pull and inject.** A machine fetches the values a process needs, at process start, into that process's memory only. Where the consumer calls an HTTPS API, the broker injects the key into the forwarded request, so the client never holds it.
- **Entry.** An agent that needs a credential not yet in the vault creates a *named request*. The owner types only the value, at a hidden terminal prompt or on a web page over Tailscale.

## Anti-goals (a failure even if it "works")

- A value printed in a terminal, log, chat or agent tool output, or written to any file on a client, including tmpfs.
- A client holding a vault password, `BW_SESSION` or a relay token.
- A machine reading an item outside its allowlist, or one org readable through the other org's relay.
- Agents choosing or typing values, or the owner typing names.

## Components

```
clients                                broker (Linux VM, TPM)                         Vaultwarden
┌──────────────────────┐  tailnet     ┌────────────────────────────────────────┐   ┌───────────┐
│ fnox → vault shim    │ ───HTTPS───▶ │ relay-front :443  (user relay-front)   │   │           │
│ relay CLI            │              │   /local → relay-local  :8441 ─────────┼──▶│ local-org │
│ vault-entry (owner)  │              │   /infra → relay-infra  :8442 ─────────┼──▶│ infra-org │
│ API calls via routes │              │   /entry → relay-entry  :8443 ─────────┼──▶│ (writes)  │
└──────────────────────┘              └────────────────────────────────────────┘   └───────────┘
```

| Piece | File | Runs as | Notes |
|---|---|---|---|
| Front door | `relay/relay_front.py` | `relay-front` | TLS with the Tailscale certificate (`relay-cert`, renewed weekly). Strips the `/local`, `/infra` or `/entry` prefix and sets `X-Relay-Peer` to the TCP peer. Answers loopback-only paths with 404. Has no vault access. |
| Read relay | `relay/relay_read.py --org local\|infra` | `relay-local`, `relay-infra` | Started by `with-bw-session`, which unlocks with the `systemd-creds` credential, writes `BW_SESSION` to a 600 file under `/run`, and wraps the relay in `fnox proxy run` when the org has `[api.*]` routes. The relay process's own environment never contains `BW_SESSION`. |
| Entry service | `relay/relay_entry.py` | `relay-entry` | Holds no session. Unlocks → writes → verifies → locks per owner submission. Requests are kept in `/var/lib/relay-entry/requests.json` (names, never values) and expire after 24 h. |
| Shared libs | `relay/relaylib/` | | `bwclient` (the only module that runs `bw`; 5-minute item cache), `routes` (parse and validate), `identity` (`tailscale whois`, cached 5 min), `requests_store`, `httpbase`. |
| Broker CLIs | `broker/bin/` | root | `relay-login`, `relay-check`, `relay-routes`, `relay-update`, `relay-cert`, `with-bw-session`. |
| Clients | `client/` | user | `relay` (check/request/status/backups), `vault` (fnox shim), `vault-entry` (owner). Stdlib Python ≥ 3.9; no proxies from the environment; redirects are errors. |

## Identity

Caller = the first DNS label of `tailscale whois --json <peer-ip>`, lowercased and cached for 5 minutes. The label counts only when the rest of the name is the broker's own tailnet suffix (`tailscale status --json`, `Self.DNSName`): a device from another tailnet (node sharing) chooses its own short name, so it is an unknown caller. An unknown peer gets 403. Tagged nodes are never owner devices. Backends trust `X-Relay-Peer` only from 127.0.0.1, so a client cannot forge it through the front door.

Identity uses `Node.Name`, not the display `ComputedName`. Trust the tailnet administrators and
prevent renamed or newly enrolled devices from inheriting stale short-name grants; stable node
IDs are not pinned. `[security].agent_nodes` cannot overlap raw-route or owner roles. Raw reads
also require `[security].trusted_value_nodes`, empty by default, in addition to route permission.

## Endpoints

Read relay (under `/local` or `/infra`):

| Method and path | Who | Returns |
|---|---|---|
| `GET /value/<name>` | `[value.<name>]` nodes | raw value |
| `GET /<project>/value/<name>[/notes]` | `[project.<project>]` nodes, item matches `items` | raw value |
| `GET /check?names=a,b/notes` | any known machine | `{"a":"ok"\|"missing"\|"not-allowed"}` |
| `GET /backups/<name>` | machines that may read `<name>` | `{"backups":["YYYYMMDD",…]}`, newest first |
| `* /<route>/<path>` | `[api.<route>]` nodes | the upstream's response; the key is added by `fnox proxy` |
| `POST /refresh`, `GET /names` | loopback secret only | for `relay-entry` |

A backup `<name>-backup-YYYYMMDD` requires the original item's raw-route permission and trusted
consumer policy. API-only callers can check their exact API item reference but cannot read it
or fetch backups. `ok` reports presence/access, not upstream authentication. Value reads are
rate-limited to 300 per minute per machine.

Entry (under `/entry`):

| Method and path | Who |
|---|---|
| `POST /v1/requests`, `GET /v1/requests/<id>` | any known machine (at most 20 pending requests per machine, then 429) |
| `GET /v1/requests?status=pending`, `POST /v1/requests/<id>/value`, `POST /v1/replace` | owner devices (`entry.toml` `owner_nodes`) |
| `GET /`, `POST /submit`, `POST /replace` | owner devices: the web page |
| `POST /v1/suspect`, `POST /v1/unsuspect` | loopback secret: read relays mark items that got upstream 401/403 |

## Writes

For each owner submission:

1. Re-validate the name and org under a lock. A second submission of the same request is refused, so a double tap can't overwrite a backup.
2. If the request is a replace and the item exists, rename it to `<name>-backup-YYYYMMDD`. If that backup already exists, refuse before mutation. Renaming keeps the old value without it ever passing through a response.
3. Create the item in the org's collection. For an `infra` request with a project, that is the nested collection `<collection>/<project>`, which must already exist. Single-line values go in Password, multi-line values in Notes, and the purpose goes in the notes.
4. Read the item back and compare SHA-256 hashes in the process. On a mismatch or error, delete the new item and undo the rename.
5. Mark the request `stored` or `replaced`, ask the matching read relay to refresh, and write an audit line (id, name, org, outcome, caller).

Names follow `^[a-z0-9]+(-[a-z0-9]+)*-(dev|qa|prod)$` with a project, or `^[a-z0-9]+(-[a-z0-9]+)*$` without one, and may never end in `-backup-YYYYMMDD`. A request for a name that is one `-segment` away from an existing item is flagged *near duplicate*, and the owner sees the flag before entering.

## Files on the broker

| Path | Content | Mode |
|---|---|---|
| `/etc/relay/routes.toml`, `entry.toml`, `vault.env` | names, URLs | 644 |
| `/etc/credstore.encrypted/bw-unlock-{local,infra,entry}` | unlock passwords, `systemd-creds` (TPM + host key) | 600 |
| `/etc/credstore.encrypted/relay-loopback` | shared loopback secret | 600 |
| `/var/lib/relay-<name>/` | `bw` data dir, `org.json` (org and collection IDs), `api.toml` (fnox references only) | 700 |
| `/var/lib/relay/tls/` | Tailscale certificate | 750, group `relay-front` |
| `/run/relay-<org>/bw.env` | `BW_SESSION` of the running read relay | 600, tmpfs |

## fnox integration

fnox 1.35's `vault` provider shells out to a `vault` CLI rather than speaking HTTP (see [fnox-vault-provider.md](fnox-vault-provider.md)). The `client/vault` shim answers exactly `vault kv get -field=<field> <path>/<name>` with one GET to `$VAULT_ADDR/<path>/value/<name>[/<field>]`. `VAULT_TOKEN` is ignored. A missing or forbidden item prints `No value found at …` and exits 2, which fnox reports normally. With `if_missing = "error"`, `fnox exec` stops instead of starting the app with an empty variable.

On the broker, `relay-routes install` turns each org's `[api.*]` routes into an fnox config (`gen-fnox-config.py`) of `[[proxy.rules]]` with exact host, methods and paths, and bitwarden-provider references. `fnox proxy run` then injects values into matching requests. fnox resolves every proxy secret before it starts the relay, so a single `[api.*]` item it cannot find (missing, or a name another item's name contains) stops that org's relay; `relay-routes install` checks the items first, waits until the relay listens, and puts the previous config back if it never does. [Git through the broker](git-through-the-broker.md) is a worked example of such a route.
