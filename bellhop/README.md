# bellhop

Run tools that hold a login on a machine with no agent, and use them from the agent's machine
as if they were installed there. The agent gets the tool's results. It never gets the login.

A valet parks the car and keeps the key. A bellhop runs the errand for you.

This is not a bulletproof solution for protecting logins from agents. It is a practical way to reduce
risk in a home agentic setup: it stops a login being copied, not an allowed tool being misused.

```
agent machine                         bellhop machine (no agent)
  higgsfield upload scene.png           server.py
    -> bellhop (stand-in)   -- HTTPS -->   checks who is calling and what they ask
       sends the command                   runs the real tool with its own login
       and the file                        in an empty folder, then cleans up
    <- output, exit code, result files <-
  MCP client -> /mcp/<name>  ---------->   adds the key, forwards to the MCP server
```

## What is here

| File | Runs on | Purpose |
|---|---|---|
| `server.py` | bellhop machine | The service: runs listed tools, forwards listed MCP servers |
| `bellhop` | agent machine | The stand-in command. Link it under each tool's name |
| `bellhop.example.toml` | bellhop machine | Configuration with one tool and one local MCP server |
| `sweep.py` | agent machine, as root | Hourly check for logins and new tools; reports names only |
| `agent-rules.md` | agent machine | Standing instructions for the agent |
| `test_bellhop.py`, `fakes/` | anywhere | Tests against a fake tool and a fake MCP server |
| `testvm/setup.sh` | Mac or an OrbStack machine | Builds two test machines with synthetic logins |
| `install-bellhop.sh`, `proxmox-bellhop-vm.sh` | bellhop VM, Proxmox host | Installs the bellhop for real use |
| `bellhop-tools.sh`, `proxmox-bellhop-run.sh` | bellhop VM, Proxmox host | Starter tools on the bellhop; run a script or open a shell in the VM |

Python 3.11 or later, standard library only. `server.py` reuses `broker/brokerlib` from this
repository for caller identity and request handling.

## Rules the bellhop enforces

- **Callers are named.** By Tailscale machine name, the same way the broker does it. Each tool
  and MCP server lists who may use it. Everyone else gets 403.
- **Only listed subcommands run.** `allow` names the leading arguments a call must start with.
  Leave out anything that prints, exports or changes the login.
- **No paths into the bellhop.** Arguments such as `/x`, `~/x`, `-o/x`, `key=/x`, `file:/x` or `../x`
  are refused. The tool runs in an empty folder that holds only the files sent with the call.
- **Files are data.** An upload is stored under a name the bellhop picks and is never run.
- **The log has names and outcomes, no values.** One line per request.

The `allow` list is the real control. The path rule is a second layer: do not list a tool or
subcommand whose purpose is to read a file you name.

## Use

Bellhop machine: copy `server.py` and `broker/brokerlib` to one folder, write `/etc/bellhop/bellhop.toml`
from the example, and run it as a user that owns the logins and has no other job:

```sh
python3 -I server.py --config /etc/bellhop/bellhop.toml
```

It listens on plain HTTP on loopback. For TLS on the tailnet, run valetkey's `broker_front.py`
in front with `--backend bellhop=127.0.0.1:8460`; callers then use `https://<bellhop>/bellhop/...`.

Agent machine, one command from the repository root:

```sh
sudo ./install.sh agent --bellhop-url https://<bellhop>/bellhop --tools higgsfield   # add --broker-url for the broker
higgsfield upload scene.png                      # runs on the bellhop
valetkey-bellhop --list
```

It installs the stand-in and one link per tool, the sweep and its timer, and the MCP policy, all
owned by root. By hand it is `install -m 755 bellhop /usr/local/bin/bellhop`, a link per tool, and
the bellhop's address in `/etc/bellhop/url` (or `BELLHOP_URL`).

For an MCP server that runs on the bellhop, point the agent's MCP client at
`https://<bellhop>/bellhop/mcp/<name>` with no key.

## On Proxmox

The bellhop belongs on a machine no agent can reach into: a VM on the hypervisor, next to the
broker, not a container or VM under a desktop account that also runs agents.

```sh
bellhop/proxmox-bellhop-vm.sh        # on the Proxmox host as root: creates VM 212 "bellhop" and installs
bellhop/install-bellhop.sh           # what it runs inside the VM; safe to re-run there
```

The installer joins the tailnet (it prints a login link, tag `tag:bellhop`), fetches the machine's
HTTPS certificate, and starts two services: `bellhop` (user `bellhop`, loopback only) and the
broker's `broker-front` as the only listener, on the tailnet address, port 443. Callers use
`https://bellhop.<tailnet>.ts.net/bellhop`. It installs no tool and no login, and refuses to run on a
machine that has an agent installed.

After that, from the Proxmox host:

```sh
bellhop/proxmox-bellhop-run.sh bellhop/bellhop-tools.sh agent-dev  # install and list a starter set of tools
bellhop/proxmox-bellhop-run.sh shell                            # a shell in the VM, for the sign-ins
bellhop/proxmox-bellhop-run.sh bellhop/install-bellhop.sh          # later: update the bellhop's own code
```

`bellhop-tools.sh` installs three example command-line tools (Higgsfield, Vercel, Stripe) and lists
them with cautious allow lists. It signs nothing in; it prints the sign-in steps. Use it as a
pattern for your own tools.

In the tailnet policy, add `"tag:bellhop": ["autogroup:admin"]` to `tagOwners` and one grant from
each agent machine to `tag:bellhop` on `tcp:443`. Nothing else should reach the bellhop.

## Adding a tool or MCP server

Step by step, for either the broker or the bellhop: [the recipe](../docs/recipe-new-tool-with-credential.md).

**An MCP server with a fixed key belongs on valetkey's broker, not on the bellhop.** A broker `[api.*]`
route does the same forwarding, keeps the key in the vault, takes the key through the entry page
and is changed only at the broker console. The agent files `valetkey-broker request NAME-api-key`, the
owner enters the value and adds the route, and the MCP client uses
`https://<broker>/<org>/<route>/...` with no key. The bellhop's `[mcp.*]` entries remain for a
server that runs on the bellhop itself.

For command-line tools:

Installing something on the bellhop does nothing until it is listed in `bellhop.toml` and the bellhop is
restarted. The agent cannot install on the bellhop. It asks, and the owner decides:

```sh
valetkey-bellhop --request mcp buffer https://mcp.buffer.com/mcp "schedule posts"   # agent machine
python3 -I server.py --requests                                              # bellhop: what is waiting
python3 -I server.py --done <id>                                             # bellhop: handled
```

A request is a note with a name, a source and a reason. The bellhop never installs or runs
anything because of one.

## Keeping the agent machine clean

Three things on the agent machine, all owned by root so the agent's user cannot change them.
`sudo ./install.sh agent` sets up all three:

- **Policy.** `/etc/claude-code/managed-settings.json` with `allowManagedMcpServersOnly` and an
  `allowedMcpServers` entry for the broker and for `https://<bellhop>/bellhop/mcp/*`. Claude Code then
  refuses any MCP server elsewhere. Local MCP servers that need no key must be listed there by name.
- **Instructions.** The same file's `claudeMd` holds `agent-rules.md`: tools that need a login
  are requested from the bellhop, not installed locally.
- **Sweep.** `sweep.py` on an hourly timer. It reports login files, key files, `.env` files and
  MCP settings that hold a key, MCP servers that bypass the bellhop, and programs that appeared in
  the user's tool folders since the known list. It prints paths and reasons, never contents.
  Expected items go in `/etc/bellhop/sweep-allow`; `sweep.py --accept` takes the current tools as
  known.

The policy covers Claude Code only. Codex reads its own settings; the sweep still reports MCP
servers in `~/.codex/config.toml`.

## Test

```sh
python3 -m unittest bellhop/test_bellhop.py     # 17 tests, fake fixtures only
bellhop/testvm/setup.sh                         # two OrbStack machines, then an end-to-end check
bellhop/testvm/drill.sh                         # rehearses install-bellhop.sh with a fake tailscale, over HTTPS
bellhop/testvm/setup.sh delete
```

## Limits

- **The agent can still use every listed tool.** This stops a login being copied. It does not
  limit what is done with it. Keep keys narrow and capped.
- **Native OAuth sign-in for MCP.** Connect an approved client directly to the provider’s MCP
  endpoint and keep its tokens off agent machines. Skip the Bellhop and broker routes.
- **MCP servers must speak HTTP.** One that only talks over standard input needs a bridge on the
  bellhop's loopback first.
- **Output arrives when the tool exits.** No live progress for long jobs.
- **Uploads are capped** at `max_upload_mb` (50 by default, which is also `broker_front`'s cap).
- **Result files come back from the top of the job folder only**, not from subfolders.
- **The sweep detects; it does not prevent.** It looks six folders deep, skips dependency
  folders, and knows a fixed list of login locations. A login hidden elsewhere is missed.
- **Installs are not blocked.** An agent can still install a program into its home folder. It
  cannot create a login for it, and the sweep reports the new program.
- **A tool that runs project code with a key cannot be made safe this way.** Tests and dev
  servers run code the agent wrote, and that code can print the key wherever it runs.
- **`identity = "address"` trusts the source address.** It is for test networks without
  Tailscale. Do not use it on a network you share.
