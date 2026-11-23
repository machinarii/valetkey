# Recipe: add a tool that needs a key or login

Use this when you want agents to use a new service, command-line tool or MCP server that needs
a credential. It applies the [keyless tool standard](keyless-tool-standard.md): the agent's
machine never holds the key, and the broker or the desk does.

If the tool needs no key or login, stop here and install it on the agent's machine as usual.

## Who does what

| Step | Agent | Owner |
|---|---|---|
| Choose the path, write the manifest, draft the settings | Yes | Reviews |
| File the request for the key, by name | Yes | |
| Enter the key or sign the tool in | Never | Yes |
| Install settings on the broker or desk | Never | Yes, at the console |
| Point the agent's side at it, then test | Yes | |
| Remove old copies of the key | Never | Yes |

## Step 1: choose the path

| The service gives you | Path | Where the key lives |
|---|---|---|
| An HTTP API or a remote MCP server, with a fixed key | **A: broker route** | In the vault |
| Only a program or SDK, or a login that renews itself | **B: desk entry** | In the desk user's home folder |
| An MCP server with native OAuth sign-in | Provider’s MCP endpoint and native sign-in | OAuth tokens stay with an approved client outside the agent VM |

For native OAuth, stop the Valetkey setup here. Skip the manifest, key request, broker route
and Bellhop entry. If the owner’s MCP policy blocks the provider endpoint or the client would
store tokens on an agent machine, leave the server unconfigured.

## Step 2: write the manifest

One manifest per key. If the tool's jobs need different rights (reading versus pushing, test
versus live), give each job its own key and item; [several keys for one
service](several-keys-per-service.md) explains why a route alone cannot split them.

Create `tool.toml`. It is a worksheet today: nothing reads it, but every later step copies from
it. Name the credential; never write its value.

```toml
[tool]
name = "acme"
summary = "What it does, in one line"

[credential]
kind = "fixed-key"            # or renewing-login
item = "acme-api-key"         # the name the key is stored under
scope = "what this key can reach; ask for the narrowest one"
modes = ["test", "live"]

[trusted]
place = "broker"              # or desk
upstream = "https://api.acme.example"
inject = "Authorization: Bearer {}"
paths = ["/v1/reports"]
methods = ["GET"]

[operations]                  # every operation in exactly one class
read = ["list reports", "get report"]
write = []
spend = []
destructive = []
credential = []               # anything that shows or changes the login
allowed = ["read"]            # start here; widen when asked
```

For path B, replace `[trusted]` with the program and the subcommands that hold the login:

```toml
[trusted]
place = "desk"
command = "/usr/local/bin/acme"
credential_prefix = ["auth", "config"]
deny_options = ["--debug", "--token"]
```

## Path A: a broker route

1. **Agent files the request for the key.** A name and a purpose, no value:
   ```sh
   relay request acme-api-key --org local --purpose "Acme reports through a broker route"
   ```
   It prints a request id.
2. **Owner enters the key**, on their own device: the broker's entry page at
   `https://broker.<tailnet>.ts.net/entry`, or `vault-entry <request id>` in a private terminal.
   Enter the bare key. Do not add "Bearer"; the route adds it.
3. **Agent requests the route** from the machine that will use it.

   ```sh
   relay request-route acme --org local --item acme-api-key \
     --upstream https://api.acme.example --method GET --path /v1/reports
   ```

   Give the owner the printed link. On a paired approver device, review the destination, scope
   and requesting machine, then approve with a fresh authenticator code. Wait for **Route active**.
   See [Pair an authenticator and approve an API route](route-approval.md) for first-time setup.
4. **Agent points the client at the broker.** The address is
   `https://broker.<tailnet>.ts.net/local/acme/...`, with no key. For an MCP server, that is the
   URL in the client's settings, with no headers.

## Path B: a desk entry

1. **Agent files the request**, so the owner sees what is wanted:
   ```sh
   bellhop --request cli acme https://acme.example/cli "what it is for"
   ```
2. **Owner installs the program on the desk** and signs it in as the desk user:
   ```sh
   sudo -u desk -H acme auth login
   ```
   If the sign-in wants a browser on the same machine, start it in the background and paste the
   failed `localhost` address at a prompt, as `bellhop/desk-tools.sh` prints for Higgsfield.
3. **Owner lists it** in `/etc/bellhop/desk.toml` and restarts the desk:
   ```toml
   [tool.acme]
   command = "/usr/local/bin/acme"
   callers = ["agent-dev"]
   allow = ["reports list", "reports get"]
   deny = ["--debug", "--token"]
   ```
   ```sh
   sudo systemctl restart bellhop-desk
   ```
   `allow` holds the leading words a call must start with. Copy them from the manifest's allowed
   classes. Leave out every subcommand under the credential prefix.
4. **Agent links the stand-in** on the agent machine, so the tool keeps its own name:
   ```sh
   sudo ln -s bellhop /usr/local/bin/acme
   bellhop --list
   ```

## Step 3: check the tool against the rules

These decide whether a tool can be used this way at all. Check them before doing the work above.

| Rule | If the tool breaks it |
|---|---|
| Runs without a local key or login | It cannot go through the broker; try the desk |
| Its service address can be changed | Same |
| Login commands sit under one prefix | List allowed subcommands one by one, with care |
| No option prints the key | Put that option on the deny list |
| Takes files as content, not as paths on the trusted side | Do not list the subcommands that read a path |
| One call, one answer; no prompts or listeners | Those subcommands will not work; leave them out |
| Works without a project folder | Those subcommands will not work; leave them out |

## Step 4: test before relying on it

From the agent machine, looking at pass or fail only, not at account data:

- every allowed operation works;
- every login operation, denied option and path-style argument is refused;
- a machine that is not listed gets nothing;
- the service works in the mode you expect (some tools need a flag for live mode).

Do not create, change or spend anything on the real account while testing, unless the owner
agreed to it.

## Step 5: switch over and clean up

1. Keep the old way working until the new one passes its tests.
2. Point the real projects at the new address or stand-in.
3. The owner removes the old key from the agent machines and, where possible, replaces it with a
   new one at the service, since the old one has been on a machine an agent could read.

## What to expect

- The agent can still use everything on the allowed list. This stops a key being copied; it
  does not limit what is done with it. Keep keys narrow and capped.
- A tool that runs the agent's own code with a key, such as a test suite, cannot be made safe
  this way.
