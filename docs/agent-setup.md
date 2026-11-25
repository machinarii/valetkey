# Set up agent machines and the desk

## Working with AI agents

**Optional memory capture.** Valetkey is designed to work with or without claude-mem. You can install Valetkey and sign in to Claude Code without a memory worker. If you already use claude-mem, `relay guide claude-mem` explains local capture and how to migrate remote ingest without granting agents a raw token.

For Claude Code, run `relay guide claude` to choose between a Valetkey-managed setup token and native web login. See [subscription authentication](secure-setup.md#claude-code-subscription-sign-in).

If you run your agents from T3 Code, see [T3 Code](t3-code.md): which machine its server belongs on, how the policy keeps T3's own tools working, and how T3 signs in to Claude.

[`agents/credential-rules.md`](../agents/credential-rules.md) is a drop-in section for `CLAUDE.md`, `AGENTS.md` or any agent instruction file. It teaches the agent to:

- check with `relay check` instead of reading values;
- create named requests and **always offer you both entry methods** (terminal or web);
- keep agents separate from trusted servers and approver devices;
- never ask for, print or store a secret, including in Notion or other cloud documents.

### Setting up Claude Code

`./install.sh claude` does this for Claude Code, and for Codex's `AGENTS.md` when Codex is installed. Run it on the agent machine as the user the agent runs as. It asks, with a recommended answer for each:

| Question | Recommended | What it changes |
|---|---|---|
| Add valetkey's rules to `CLAUDE.md`? | `y` | A marked block in `~/.claude/CLAUDE.md`, replaced in place on later runs. The desk's rules are included when the machine has a desk address |
| Turn on the command sandbox? | `y` | `sandbox.enabled`; the broker's and desk's hosts are added to the allowed network list |
| Block reading login files? | `y` | Read rules and sandbox read blocks for SSH keys, cloud logins, tokens and shell history, at their fixed places in your home folder. No `**/.env`-style patterns: Claude Code turns each into one sandbox entry per matching file, and across a home folder that stops every command from starting. The agent rules cover `.env` and key files |
| Block commands that print a secret? | `y` | Deny rules for `env`, `printenv`, `gh auth token`, `vault`, password-manager commands and similar |
| Turn off claude.ai account connectors? | `y` | `disableClaudeAiConnectors`. Connectors are logins held on Anthropic's side that any session signed in as you can be offered |
| Never run a command outside the sandbox? | `n` | `sandbox.allowUnsandboxedCommands: false`. Safer, but tools that must leave the sandbox stop working |

When `sudo ./install.sh agent` has already put the rules in the root-owned policy, the first question is skipped: the same rules twice would cost their tokens twice.

It then asks for the names of any environment variables to remove from the agent's commands (names only, never values), shows every change, and writes only when you enter `y`.

- **Run it again to change an answer.** A no removes what an earlier yes added, and a new broker or desk address replaces the old one. Rules and settings you added yourself are never removed: what valetkey added is recorded in `~/.claude/valetkey-setup.json`.
- **It backs up.** Each file is copied to `<name>.before-valetkey-<time>` first.
- **`--yes`** takes every recommended answer; **`--dry-run`** shows the changes and writes nothing.

These are the agent user's own settings, so that user can change them back. They cut down accidents; they are not a boundary. The rules an agent cannot undo come from `sudo ./install.sh agent`, which writes root-owned files.

[`bellhop/agent-rules.md`](../bellhop/agent-rules.md) is the matching section for machines that use the desk: call listed tools by name, file a request for a tool that needs a login, and treat a refusal from the desk as final.

## The desk: tools with their own login

Some tools cannot be reached through an API route. They are programs that sign in and keep a login file, and they expect to run where that file is. bellhop runs them on the desk and gives agent machines a stand-in.

```
agent machine                         desk (no agent)
  sometool upload scene.png             checks who is calling and what they ask
    -> bellhop (stand-in)   -- HTTPS -->   runs the real tool with its own login,
       sends the command and the file      in an empty folder, then cleans up
    <- output, exit code, result files <-
```

| The desk enforces | How |
|---|---|
| Who may call | Tailscale machine name, per tool. Everyone else gets `403` |
| What may run | Only subcommands on the tool's `allow` list. Login and token commands are left off it |
| No reaching into the desk | Arguments shaped like a path on the desk are refused: `/x`, `~/x`, `-o/x`, `key=/x`, `file:/x`, `../x` |
| Files are data | An upload is stored under a name the desk chooses and is never executed |
| A record | One log line per request: machine, tool, outcome. Never a value |

Which goes where:

| The service offers | Use |
|---|---|
| An HTTP API or remote MCP server with a fixed key | A broker `[api.*]` route. The key stays in the vault |
| Only a program or SDK, or a login that renews itself | A desk entry |
| An MCP server with native OAuth sign-in | Use the provider’s endpoint directly from a policy-approved client that keeps tokens off agent machines. Skip Valetkey routing. |

Start with [bellhop/README.md](../bellhop/README.md). To add a tool step by step, follow [the recipe](recipe-new-tool-with-credential.md). [The keyless tool standard](keyless-tool-standard.md) is a draft of how a tool should be built so that either the broker or the desk can hold its credential.
