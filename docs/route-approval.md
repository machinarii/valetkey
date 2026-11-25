# Pair an authenticator and approve an API route

valetkey 0.1.2 adds two separate links. A broker administrator issues a **pairing link** for one approver device. An agent requests an **approval link** for one proposed route. The ordinary credential entry page still handles key storage.

## Pair once

1. On the trusted broker, install the 0.1.2 broker services from a reviewed checkout with `sudo ./install.sh broker`. The QR page uses the distribution's `qrencode` package. On Debian or Ubuntu, install it with `sudo apt-get install qrencode`. The broker installer also includes it with `--with-deps`. The `./install.sh update` command refreshes the version CLI.
2. Choose the Tailscale machine name of an untagged approver device already listed in `/etc/valetkey-broker/entry.toml` under `owner_nodes`. Keep agents on separate machines in `routes.toml` under `security.agent_nodes`.
3. At the broker console run the following, substituting that device's name for `laptop`.

   ```sh
   sudo valetkey-broker approval pair laptop
   ```

4. Open the printed HTTPS link on that approver device over Tailscale within ten minutes. In your authenticator choose **Add account → Scan QR code**. Scan the QR shown on the approver device and enter the six-digit code on that same page. **Authenticator paired** confirms enrollment. The QR contains the shared TOTP secret. Keep it out of chat, screenshots, synced notes and agent sessions.

The broker renders the QR locally. Opening another pairing link for the same device cancels its pending link. To replace a lost generator, repeat the console command and confirm the new generator. The previous generator remains active until confirmation. Pair each additional approver device separately. Keep the broker clock and authenticator clock synchronized.

## Approve a route

For a fixed key already stored in the correct Vaultwarden collection, run this on the agent that will use it. Routes send the key as a Bearer token unless you ask for Basic (below). Use the provider's documented HTTPS origin, methods and narrowest required paths.

```sh
valetkey-broker request-route reports --org local --item reports-api-key \
  --upstream https://api.example.com --method GET --path /v1/reports
```

Open the printed approval link on a paired approver device within fifteen minutes. Verify the requesting machine, vault item, provider domain, methods and path prefixes, including their descendants. The provider receives the credential when the agent uses this route. Enter a fresh authenticator code and choose **Approve this route**. Wait for **Route active**. A code used for pairing or another approval cannot be reused.

For a missing key, first file `valetkey-broker request` and use the broker's separate entry page or `valetkey-entry <id>` in the owner's private terminal. When the route is active, check storage and access with `valetkey-broker check local reports-api-key`. Verify upstream authentication with a harmless allowed call. For a service you sign in to instead of pasting a key, see [Services you sign in to](oauth-sign-in.md).

### Services that use HTTP Basic

Some services take the key as a Basic user name with an empty password. The broker sends a stored
value as it is, so the vault must hold the encoded form. Ask for both with the two `basic` options:

```sh
valetkey-broker request motion-api-key-basic --org local --purpose "Motion API" --encode basic
valetkey-broker request-route motion --org local --item motion-api-key-basic --auth basic \
  --upstream https://api.example.com --method POST --path /graphql
```

On the entry page you paste the key as the service shows it; the broker stores base64 of the key
and a colon. The approval page then says the key is sent as "Basic (stored value)".

## Join a route another machine already uses

When a route exists for one machine and another machine needs the same key on the same service,
run this on the machine that needs it:

```sh
valetkey-broker join-route reports
```

The approval page says which machine asks, which key the route sends to which address, how the key
is sent and which machines already use it. Approving adds only that machine to the route. Its
address, key, auth format, methods and paths stay as they are, so this also works for routes you
wrote by hand with another auth format, such as Basic.

The join is kept as `/etc/valetkey-broker/approved-routes.d/<route>+<machine>.json`. It applies only while the
route keeps the same org, key, address and auth format it had when you approved it. If you later
point the route at another address or key, the joined machine loses it until it asks again. Paths
and methods you change by hand apply to every machine on the route, joined ones included. A join
cannot add a machine to a model route.

## An MCP route limited to some tools

A route to an MCP server lets the machine call every tool the key allows, unless you limit it:

```sh
valetkey-broker request-route notes --org local --item notes-api-key \
  --upstream https://mcp.example.com --method POST --path /mcp --tools read-only
```

`--tools read-only` allows only the tools the server itself labels read-only (`readOnlyHint`); the
broker asks the server for its tool list every ten minutes and refuses every call when it cannot
get it. `--tools search,fetch` allows exactly those names. In `routes.toml` the same setting is
`tools = "read-only"` or `tools = ["search", "fetch"]`. The approval page shows the limit.

The broker reads each request before it forwards it. A call to another tool gets an MCP error that
names the tool, and the audit line says `tool refused: <name>`. A request that is not JSON-RPC, or
that repeats a key, is refused. The machine still sees the full tool list; the limit is on calling.
The read-only labels are the server's own word, so for a service you do not trust, name the tools.

## Access that ends by itself

The approval page asks how long the access lasts: until you remove it (the default), 1 hour,
8 hours, 1 day, 7 days or 30 days. Choose a limit for a route that can write or delete, or for a
job that is done in an afternoon.

When the time is up the broker refuses the route's calls with "the owner's approval of this route
has ended", and `valetkey-broker check` reports the key as `not-allowed` for that machine. The
machine can ask again with the same `request-route` or `join-route` command. A limited route ends
for every machine on it, also for machines that joined it later; a limited join ends for that
machine only.

A limited approval is kept as `<route>.grant` (or `<route>+<machine>.grant`) with its end time. A
broker version from before 0.6 reads only the `.json` files, so if you roll back, a limited
approval is gone rather than permanent.

## broker operations

The worker installs new bearer API grants, and joins to existing API routes, for the requesting machine only. It stores grants in `/etc/valetkey-broker/approved-routes.d` and leaves the administrator's TOML in place. Bearer and Basic are supported; manual routing remains available for other authentication schemes. To revoke a web grant or a join, the administrator removes its JSON file and runs `sudo valetkey-broker routes install <org>` and verifies denial from the affected agent. Restrict root access to this directory and the broker. Keep the vault, hypervisor, broker and approver device outside agent control.

If activation fails, check `systemctl status valetkey-broker-approval.service`, `systemctl status valetkey-broker-activate@local.service` (or `infra`) and `sudo valetkey-broker check <org>` at the trusted broker console. An interrupted installation is rolled back when the worker starts. If recovery fails, resolve the reported broker or vault issue, restart the worker, then file a fresh request. The worker's private database and its encrypted master credential together protect authenticator enrollments. Protect both when backing up the broker.
