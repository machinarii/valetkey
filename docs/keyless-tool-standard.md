# The keyless tool standard (draft 1)

A tool meets this standard when the machine an agent runs on never holds the tool's key or
login, and a second machine with no agent decides what the agent may do with it.

Status: a proposal, written from running valetkey's broker and bellhop. Nothing reads
`tool.toml` yet; the mapping table below is applied by hand.

To add a tool by following steps, use the [recipe](recipe-new-tool-with-credential.md).

## Scope

This standard covers only tools that need a credential: an API key, a token, or a login that
the service checks. That includes command-line tools, MCP servers and code libraries that call
such a service.

A tool that needs no credential is out of scope. Compilers, linters, formatters, local file
tools and anything that only reads public data are installed and run on the agent's machine in
the ordinary way. They need no manifest, no broker route and no bellhop entry, and nothing here
applies to them. A manifest without a `[credential]` table is not a valid manifest.

If a tool has some operations that need a credential and some that do not, the standard covers
the ones that do. The others may stay local.

## Why the usual pattern fails

| Usual pattern | What goes wrong |
|---|---|
| The tool reads a key from its environment or a file on the agent's machine | Any command the agent runs can read it too |
| An MCP client holds the server's sign-in | The client is the agent's own program, on the agent's machine |
| "Don't print secrets" in the instructions | A rule the model can be talked out of; not a boundary |
| Code execution: the agent writes programs that call the tool's wrappers | A wrapper that holds a key shares a sandbox with code the agent wrote |

Code execution changes how tools are presented, not where the key may live. Anthropic's write-up
shows tools as files of code that the agent reads on demand and composes in a sandbox, keeping
intermediate results out of the model's context. Cloudflare's Code Mode gives the sandbox no
network and hands it bindings, so that "the AI cannot possibly write code that leaks any keys":
a supervisor outside the sandbox adds them. This standard is the same idea with ordinary
machines: the broker or bellhop is the supervisor, and the wrappers are the bindings.

## The two halves

| | Agent half | Trusted half |
|---|---|---|
| Runs on | The agent's machine or sandbox | A machine with no agent |
| Holds | Names, addresses, wrappers | The key or login |
| Trust | None needed; treat as public | Changed only by the owner at its console |
| Knows the caller by | Nothing | Machine identity the model cannot forge |

Two kinds of trusted half exist today:

- **Broker route.** For a service reached over HTTPS with a fixed key. The broker forwards the
  request and adds the key. The key lives in the vault.
- **Bellhop entry.** For a tool that must run as a program with its own login. The bellhop runs listed
  subcommands in an empty folder and returns the output.

## The manifest: `tool.toml`

```toml
[tool]
name = "buffer"
summary = "Schedule and read social posts"

[credential]
kind = "fixed-key"              # fixed-key | renewing-login. Required: no credential, no manifest.
item = "buffer-api-key"         # the name it is stored under. Never the value.
scope = "one workspace, posting only"
modes = ["live"]                # say so if test and live differ

[trusted]
place = "broker"                # broker | bellhop
upstream = "https://mcp.buffer.com"
inject = "Authorization: Bearer {}"
paths = ["/mcp"]
methods = ["GET", "POST", "DELETE"]

[operations]                    # every operation appears in exactly one class
read = ["list_posts", "get_profile"]
write = ["create_post"]
spend = []
destructive = ["delete_post"]
credential = []
allowed = ["read", "write"]     # the classes callers get; start with read

[data]
uploads = false                 # may the caller send files
max_upload_mb = 0
returns_sensitive = ["email"]   # fields a wrapper should not pass to the model unasked
```

The operation names above are examples, not Buffer's real ones.

For a bellhop tool, `[trusted]` names the program and the lists are subcommands:

```toml
[trusted]
place = "bellhop"
command = "/usr/local/bin/higgsfield"
credential_prefix = ["auth", "workspace"]   # never listed for callers
deny_options = ["--debug"]
timeout = 900

[operations]
read = ["model", "workflow", "voices", "account", "generate list", "upload list"]
write = ["upload create"]
spend = ["generate create"]
credential = ["auth", "workspace"]
allowed = ["read", "write", "spend"]
```

### Mapping to today's settings

| Manifest | Broker `routes.toml` | Bellhop `desk.toml` |
|---|---|---|
| `tool.name` | `[api.NAME]` | `[tool.NAME]` |
| `credential.item` | `item` | none; the login is in the desk user's home |
| `trusted.upstream`, `inject` | `upstream`, `auth` | none |
| `trusted.paths`, `methods` | `paths`, `methods` | none |
| operations in the allowed classes | none; narrow with `paths` and `methods` | `allow` |
| `trusted.deny_options` | none | `deny` |
| machines that may call | `nodes` | `callers` |

The list of machines is not in the manifest. The author describes the tool; the owner decides
who gets it.

## Design rules for the tool itself

Each rule comes from something that went wrong, or worked, with a real tool.

1. **Take no credential as input.** No key option, no key variable, no login file required to
   start. A tool that refuses to run without a local login cannot be used this way at all.
2. **Make the address a setting.** One variable or option for the base address. This is what lets
   a broker stand in for the service.
3. **Prefer a fixed, narrow key.** For a login that renews, support a sign-in that works on a
   machine without a browser (a code to type on another device), and never require the callback
   to reach the machine the tool runs on.
4. **Keep credential commands under one prefix.** `auth login`, `auth token`, `config`. Then one
   line excludes them all. Never put a work command under that prefix.
5. **No option that reveals the key anywhere else.** Debug and verbose output must redact the
   authorization header. A `--show-token` on a work command breaks the whole scheme.
6. **Sort operations by risk** and say which class each is in. MCP's read-only and destructive
   hints are advice to the client; the same classes in the manifest are what the trusted half
   enforces.
7. **Files travel as content.** Accept an upload, return a download. No argument that is a path
   on the trusted side, in any spelling: `/x`, `~/x`, `-o/x`, `key=/x`, `file:/x`, `../x`.
8. **One call, one answer.** No prompts, no long-running listeners, no waiting on the terminal.
   Exit codes mean something. Offer `--json`.
9. **Do not depend on a project folder.** Take the project as an argument. A command that only
   works inside a linked folder cannot run on the trusted side.
10. **Never return the credential.** No echo, reflect or "show my headers" operation. Errors
    carry no request headers.
11. **Make test and live explicit**, and let the key be capped where the service allows it.
    Hiding a key stops copying. It does not limit what is done with it.
12. **Return less.** Page results, name sensitive fields in the manifest, and let the caller ask
    for detail. In code execution the wrapper filters before the model sees anything.

## The agent half as code

For agents that run code, generate from the manifest:

```
tools/
  index.md                 one line per operation: name, class, what it does
  buffer/
    list_posts.py          typed function, docstring, calls the broker address
    create_post.py
```

Each file is a few lines that call the trusted half's address. It imports no key and reads no
login. The agent searches `index.md`, opens only the files it needs, and writes a short program
that calls them and reduces the results. The same manifest can also give a stand-in command for
shell-only agents and an MCP endpoint for clients that only make tool calls.

## Tests every tool needs

Run all of these with a fake key against a fake service.

| Test | Expect |
|---|---|
| Each operation in an allowed class | Works |
| Each operation in a class not allowed | Refused by the trusted half |
| Each `credential` operation | Refused |
| Each denied option, alone and as `--opt=value` | Refused |
| Each path spelling in rule 7, sent directly to the trusted half | Refused |
| A caller that is not listed | Nothing, not even the list |
| The fake key, searched for in all output, errors and logs | Not found |
| The agent machine, searched for the fake key | Not found |

## What this does not solve

- The agent can still use every operation it is allowed. Scope and caps stay separate work.
- A tool that runs the agent's own code with a key (tests, a dev server) cannot be made safe.
- A sign-in that renews through a browser needs a trusted half that can renew it. None exists yet.
- The owner still has to check that an address really belongs to the service before adding it.
