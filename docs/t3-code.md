# Using valetkey with T3 Code

[T3 Code](https://github.com/pingdotgg/t3code) is an app that runs Claude Code and Codex sessions
for you, in a desktop window or a browser. It has two parts: a **server** that starts the agents,
and a **client** you look at. valetkey treats the machine where the T3 server runs as an **agent
machine**: it holds no keys, and agents reach services through the broker and the bellhop.

## Where each part runs

| Part | Where | Notes |
|---|---|---|
| T3 server | An agent machine | Install valetkey's agent role there, as for any agent machine. |
| T3 client | Any of your devices | It only shows the sessions; the agents run on the server. |
| Key entry | Your approver device, outside T3 | Never in T3's built-in browser, preview pane or terminal, and never in chat. |

Install the agent role on the T3 server machine:

- **Linux:** `sudo ./install.sh agent --broker-url https://broker.<tailnet>.ts.net --user <you>`, or
  pick *Agent machine* in the installer menu.
- **macOS:** `brew install valetkey-agent`, then `valetkey-agent-setup`.

Then set up Claude Code and Codex as in [Agent setup](agent-setup.md).

## T3 Code's own tools

T3 gives each Claude session it starts an MCP server named `t3-code`, at its own address
`<T3 server origin>/mcp`, with a token for that session only. With T3's default port that address is
`http://127.0.0.1:3773/mcp`. The tools let an agent read and start T3 threads and show previews.
They run inside T3 on the same machine and hold no provider keys.

On Linux the agent role writes a Claude Code policy, `/etc/claude-code/managed-settings.json`,
that allows MCP servers only at the broker and the bellhop. If T3 Code is installed for the agent's
user, the installer also allows T3's address, so T3's tools keep working:

- It reads T3's address from that user's `~/.t3/userdata/server-runtime.json` and falls back to
  `http://127.0.0.1:3773`.
- It accepts only `http://127.0.0.1:PORT` or `http://localhost:PORT`. The agent's user writes
  that file, so it must not be able to point the root-owned policy at another server.
- `--t3-url http://127.0.0.1:PORT` sets the address yourself; `--no-t3` leaves T3 out.

The installer never changes a policy that already exists. To add T3 to an older one, remove it and
run the installer again (same answers as before):

```sh
sudo rm /etc/claude-code/managed-settings.json
curl -fsSL https://raw.githubusercontent.com/machinarii/valetkey/main/install.sh | bash
```

Start a new T3 session afterwards and run `/mcp` in it: `t3-code` should be connected. Every other
MCP server is still blocked unless it is on the broker or the bellhop.

The policy covers Claude Code only. Codex reads its own settings, and T3's tools work there
without a change. valetkey's macOS agent package writes no policy.

## Signing in to Claude in T3

T3 asks which Claude program to run. That choice decides how Claude signs in; see
[Claude Code subscription sign-in](secure-setup.md#claude-code-subscription-sign-in).

- **Web login:** choose the native `claude` program, run `claude auth login` once as the same user,
  and give T3 no token or API key in its launch environment.
- **Setup token:** choose a launcher that you, the owner, write. It asks the broker for this
  machine's setup token on each start and passes it only to Claude. The broker answers only the
  machine named in its `claude_setup_token = true` route. For example, saved as
  `~/.local/bin/claude-t3` and made executable:

  ```sh
  #!/bin/sh
  # Claude Code for T3, signed in with the setup token the broker grants this machine.
  token=$(curl -fsS --proto =https "https://broker.<tailnet>.ts.net/local/value/claude-code-oauth-<machine>") \
    || { echo "claude-t3: the broker did not return the setup token" >&2; exit 1; }
  CLAUDE_CODE_OAUTH_TOKEN=$token exec /full/path/to/claude "$@"
  ```

  Any process on the machine can run this launcher and so use the subscription; that is the
  trade-off the setup-token route accepts. Agents must not run it or print what it fetches.
- **API credits:** point T3 at a wrapper that runs `valetkey-broker model`, as in
  [Model API accounts](model-api-accounts.md). Don't put provider API keys in T3's own settings:
  they would sit on the agent machine.

Check the choice with one short model turn in T3.

## The hourly sweep and T3 worktrees

T3 can give each thread its own git worktree, under `~/.t3/worktrees/`. A worktree is a full copy of
the project, so anything the sweep reports in a project (an `.env` file, an MCP server that does not
go through the broker) is reported again for each worktree. Fix it in the project, then remove old
worktrees. For files that only look like keys, such as `.env.example` templates, add a pattern to
`/etc/bellhop/sweep-allow`:

```text
*/.env.example
```

## Keep the T3 server private

T3's server can listen on every network interface. Reach it only over your tailnet, from your own
devices, as you would the broker. Anyone who can use T3 can start agents on that machine.

## Checklist

- [ ] The T3 server machine is an agent machine in the broker's `[security].agent_nodes`.
- [ ] `/mcp` in a T3 Claude session shows `t3-code` connected; other MCP servers go through the broker or bellhop.
- [ ] Claude signs in the way you chose, verified with one model turn.
- [ ] No provider keys in T3's settings, and keys are entered only on your approver device.
