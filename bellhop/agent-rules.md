# Tools that need a login live on the desk, not on this machine

This machine holds no keys and no logins. Tools that sign in to a service run on a separate desk
machine, and you use them from here through `bellhop`.

- See what is available with `bellhop --list`. Listed tools are called by their own name, for
  example `higgsfield upload scene.png`. Files you name are sent along; result files come back.
- When the user asks for a command-line tool that needs a key, a login or an account: do not
  install it here and do not sign in here. File a request with `bellhop --request cli NAME SOURCE WHY`,
  and tell the user the request id and that the owner installs it on the desk. It appears in
  `bellhop --list` when it is ready.
- When the user asks for an MCP server that uses a fixed key: it goes through the credential
  broker, not this machine and not the desk. File a request for the key by name with
  `relay request NAME-api-key --org local --purpose "..."`, then give the user the route to add
  on the broker console (the server's address, the machines that may use it). The owner types
  the key on the broker's entry page; you never see it. Point the MCP client at the broker's
  address for that route, with no key.
- An MCP server that signs in through a browser cannot go through either yet. Say so.
- Tools that need no login (compilers, linters, project dependencies) are installed here as usual.
- MCP servers are only allowed at the addresses the owner's policy names. Others are blocked;
  do not try another way to add one.
- Never ask for a key, and never write one into a file, a setting or an environment variable here.
- If the desk refuses a subcommand or an argument, that is deliberate. Say what was refused and
  stop; do not work around it.
- A scheduled check reports any login or new tool that appears on this machine to the owner.
