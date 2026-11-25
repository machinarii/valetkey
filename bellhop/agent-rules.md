# Tools that need a login live on the bellhop, not on this machine

External tools that require a login run on the bellhop, a separate machine, and are called from here by name.
Claude Code’s own subscription sign-in follows the machine-specific owner approval in the
credential safety rules. Only the owner handles that login.

- See what is available with `valetkey-bellhop --list`. Listed tools are called by their own name, for
  example `higgsfield upload scene.png`. Files you name are sent along; result files come back.
- When the user asks for a command-line tool that needs a key, a login or an account: do not
  install it here and do not sign in here. File a request with `valetkey-bellhop --request cli NAME SOURCE WHY`,
  and tell the user the request id and that the owner installs it on the bellhop. It appears in
  `valetkey-bellhop --list` when it is ready.
- When the user asks for an MCP server that uses a fixed key: it goes through the credential
  broker, not this machine and not the bellhop. File a request for the key by name with
  `valetkey-broker request NAME-api-key --org local --purpose "..."`, use `valetkey-broker request-route` from the consuming machine and give the owner
  its approval link. The owner enters a missing key on the broker's entry page and approves
  the proposed route with their paired authenticator. Point the MCP client at the broker's
  address for that route, with no key.
- For an MCP server with OAuth sign-in: it also goes through the broker. File
  `valetkey-broker request NAME-oauth --org local --purpose "..." --oauth ADDRESS`; the owner signs in
  on the broker's entry page. Then use `valetkey-broker request-route` with `--auth oauth`. Never
  sign in to it from this machine. If the service cannot be used this way, stop setup.
- Tools that need no login (compilers, linters, project dependencies) are installed here as usual.
- MCP servers are only allowed at the addresses the owner's policy names. Others are blocked;
  do not try another way to add one.
- Never ask for a key, and never write one into a file, a setting or an environment variable here.
- If the bellhop refuses a subcommand or an argument, that is deliberate. Say what was refused and
  stop; do not work around it.
- A scheduled check reports any login or new tool that appears on this machine to the owner.
