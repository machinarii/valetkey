# fnox `vault` provider — wire format (spike, 2026-11-06)

Historical integration notes. Raw-key injection is now restricted to separate trusted servers
in `[security].trusted_value_nodes`; it must never run on an agent machine. See [secure setup](secure-setup.md).

Run on a Debian dev machine (`fnox 1.35.3`) in a scratch dir with `broker/tools/vault-stub.py` and a logging `vault` shim. No real secrets involved; stub values only.

## Finding 1: the provider shells out to the HashiCorp `vault` CLI, it does not speak HTTP

With the stub HTTP server on `127.0.0.1:8099` and no `vault` binary on PATH:

```
WARN fnox_core::secret_resolver: Error resolving secret 'X': HashiCorp Vault: CLI tool 'vault' not found
```

The stub received no request at all. So a Vault-compatible HTTP API on the broker is useless on its own; the client needs a `vault` executable on PATH.

## Finding 2: exact CLI calls

Config used:

```toml
[providers.broker]
type = "vault"
address = "https://broker.example.ts.net/infra"
token = "tailscale"
path = "shop"

[secrets]
X = { provider = "broker", value = "database-url-shop-qa" }
N = { provider = "broker", value = "pem-shop-qa/notes" }
```

Calls made (one per secret, stdin empty, output read from stdout, trailing newline stripped):

```
vault kv get -field=value shop/database-url-shop-qa
vault kv get -field=notes shop/pem-shop-qa
```

Environment given to the child: `VAULT_ADDR=<address>` and `VAULT_TOKEN=<token>` (the config `token` is passed through; nothing else needs to be in the environment). Without `path`, the prefix is `secret/`. A reference `name/field` becomes `-field=<field>` on `<path>/<name>`, so the `ITEM/notes` convention maps to the Notes field for free.

Valid provider fields (from the TOML error): `address`, `path`, `token`, `namespace`, `credential_command`, `auth_command`, `daemon_cache`. There is no `mount` field.

## Finding 3: failure behaviour

When the CLI exits non-zero (shim printed `No value found at …` on stderr, exit 2):

- default: `WARN … HashiCorp Vault: command failed: No value found at shop/missing-item-shop-qa`, the variable is set empty, `fnox exec` continues, exit 0.
- `--if-missing error`: `ERROR …`, `fnox::provider::cli_failed`, fnox exit 1, the command does not run.

The shim's stderr text is shown to the user verbatim, so the broker client must never put a value in an error message (it has none to put, but it must not echo response bodies on error either).

Each secret is resolved by a separate CLI call; a project with ~75 secrets therefore makes ~75 calls per `fnox exec`. With `FNOX_DAEMON=off` there is no caching; each call is one HTTPS GET to the broker (~tens of ms on the tailnet). Acceptable; `daemon_cache` is not used.

## Decision for the design and plan

- Keep `fnox exec` as the user-facing command. Provide a **`vault` shim** (`client/vault`, stdlib Python, installed on each client as `~/.local/bin/vault`) that implements exactly `kv get -field=<field> <project>/<name>`: it does `GET $VAULT_ADDR/<project>/value/<name>` (plus `/<field>` when the field is not `value`), prints the body, exits 2 with `No value found at <project>/<name>` on 404/403, and never prints anything else. `VAULT_TOKEN` is ignored (identity is the Tailscale machine). Anything but `kv get` → exit 2 `unsupported`.
- The read service's project endpoint is therefore plain: `GET /<project>/value/<ref>` returning the raw value (`ref` = `name` or `name/notes`), authorised by the `[project.*]` entry's `nodes` and `items`. No Vault-KV emulation, no `/v1/sys/health`.
- The generated client `fnox.toml` uses `address = "https://broker.<tailnet>.ts.net/<org>"`, `path = "<project>"`, `token = "tailscale"`.
- `broker exec` (the fallback in the plan) is not needed.
