# Services you sign in to (OAuth)

Some services have no key to paste. You sign in with your own account in a browser, and the
service gives out a token that runs out after about an hour and has to be renewed. Most remote
MCP servers work this way. From 0.6 the broker can hold such a sign-in: you sign in once on the
entry page, the broker keeps the token in the vault, renews it, and adds it to the calls of the
machines you allow. No token reaches an agent machine.

## What it needs from the service

- OAuth 2.1 the way MCP servers offer it: the server describes its sign-in at
  `/.well-known/oauth-protected-resource` or `/.well-known/oauth-authorization-server`.
- It lets a client register itself (dynamic client registration). A service that wants an app
  registered by hand in its developer console is not supported yet; the entry page says so.
- It accepts the broker's address as the place to send you back to:
  `https://<your broker>/entry/oauth/callback`.

## Set it up

On the machine that will use the service:

```sh
valetkey-broker request notes-oauth --org local --purpose "notes MCP server" \
  --oauth https://mcp.example.com/mcp
```

On your approver device, open the broker's entry page. The request shows the service's host name
and a **Sign in** button instead of a value field. Check that the host is the service you expect,
press the button and sign in at the service with your own account. The service sends you back to
the entry page, which stores the token and shows what it opens.

Then ask for the route, with `--auth oauth`, and approve it as usual:

```sh
valetkey-broker request-route notes --org local --item notes-oauth --auth oauth \
  --upstream https://mcp.example.com --method POST --method GET --path /mcp
```

Point the MCP client at `https://<your broker>/local/notes/mcp`, with no key and no sign-in of
its own. `--tools read-only` and a time limit on the approval work for these routes too.

In `routes.toml` the same route has `oauth = true` next to `auth = "Bearer {}"`.

## How it works

- The vault item's value is the current access token. Its notes hold what a renewal needs: the
  token address, the client the broker registered, the refresh token and the time the access
  token ends. Only the broker's own vault accounts can read it.
- A minute before the token ends, the next call makes the read service ask valetkey-broker-entry
  to renew it. The entry service is the only one with a vault account that can write; it stores
  the new token in place and keeps no backup of the old one. That call waits a few seconds.
- A route with a sign-in does not go through fnox: fnox reads a value once when the service
  starts, and a token changes every hour. The read service adds the token itself and connects
  straight to the service, checking its certificate against the system's authorities.
- The broker masks the token if the service's reply repeats it, as for any other key.

## When the sign-in ends

If the service withdraws the sign-in, or it cannot be renewed, calls get `401` with the command
to ask again, and the item appears under "May be expired" on the entry page. Ask again with
`--replace` and sign in once more:

```sh
valetkey-broker request notes-oauth --org local --purpose "notes MCP server" \
  --oauth https://mcp.example.com/mcp --replace
```

## Limits

- The sign-in addresses are fetched by the broker, so they must be public `https` addresses on
  port 443. Names on your tailnet, `.local` names and private addresses are refused, and the
  broker connects only to the address it checked.
- The broker asks for the scopes the service lists for itself. It cannot ask for fewer; limit the
  route with methods, paths and `--tools` instead.
- A sign-in you start has ten minutes to finish, from the same device.
- Tested against a synthetic OAuth server only. Services differ in details; if one refuses, the
  entry page shows the step that failed.
