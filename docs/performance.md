# Token and latency measurements

## What it costs: tokens and time

Keeping keys off the agent's machine is not free. It adds text to every request the agent makes and a network hop to some commands. These are measurements, not estimates.

**Short version:** the full setup adds about **5,800 tokens to every request** (15% on top of Claude Code's own prompt), tool calls through the desk take about **90 ms longer**, requests through the broker about **50 ms longer**, and the model answers no slower. The sandbox setting has one side effect on cost that is worth knowing, [described below](#the-sandbox-and-new-sessions).

### Tokens

Claude Code sends its whole prompt with every turn of a session, so anything added to it is paid on each turn.

| What is added | Tokens per request | Notes |
|---|---:|---|
| Nothing (Claude Code's own prompt) | 38,308 | The baseline |
| valetkey's rules, from `CLAUDE.md` or the root-owned policy | +2,483 | The broker's and the desk's rules together, about 7,000 characters |
| Command sandbox turned on | +2,964 | Claude Code's own description of the sandbox to the model |
| Blocked login and key files, as listed inside that description | About +370 | Only when the sandbox is on |
| Permission rules for the Read tool and secret-printing commands | 0 | Enforced by Claude Code; never shown to the model |
| Account connectors turned off | 0 | Can only lower the count, by removing their tool descriptions |
| **Everything `./install.sh claude --yes` adds** | **+5,820** | 15% more than the baseline; 2.9% of a 200,000-token context |

Calls themselves add nothing:

| Call | Added tokens |
|---|---:|
| A tool run through the desk | 0. The output was byte-for-byte the same as running the tool directly (589 and 7,756 bytes in the two commands tested) |
| A command the desk refuses | About 15: one line saying it is not on the list |
| A request through the broker | 0. Same response as the direct call |

Over a whole task the fixed part is multiplied by the number of turns. A two-turn task (run one tool through the desk, report the result) used 76,793 input tokens without the setup and 88,621 with it: 15% more.

Most of those tokens are cheap. Within a session, Claude Code's prompt is cached after the first turn, and the added text is cached with it.

### The sandbox and new sessions

Claude Code can reuse its cached prompt across sessions started a few minutes apart in the same folder. With the sandbox on, in the version tested, it does not: each new session sends about 27,000 tokens at the full price again.

| A one-line prompt in a new session | Without the sandbox | With the sandbox |
|---|---:|---:|
| First session | $0.21 | $0.24 |
| Each session started soon after | $0.008 | $0.22 |

The figures are the API prices Claude Code reports. On a subscription they count against your usage limit instead.

- **Long interactive sessions barely notice.** The cost is paid once per session.
- **Scripts that start many short sessions do.** A loop of `claude -p` calls costs far more with the sandbox on.
- **This is Claude Code's behaviour, not text valetkey sends.** It may change between versions, so measure your own.

The sandbox is still the recommended answer: it is what stops an agent's commands from reading files and reaching hosts you did not allow. If you run many short sessions and the cost matters more, answer No to that one question and keep the rest.

### Time

| | Without | With | Added |
|---|---:|---:|---:|
| Model response to a short prompt (10 runs each, alternating) | 700 ms | 697 ms | Nothing measurable |
| A two-turn task with one tool call, start to finish (3 runs each) | 3.3 to 3.5 s | 3.4 to 3.9 s | Within the spread |
| A tool command that needs no network, direct and then through the desk | 3 ms | 95 ms | 92 ms |
| A tool command that calls its service, direct and then through the desk | 1,116 ms | 1,185 ms | 69 ms (6%) |
| A command the desk refuses | n/a | 87 ms | n/a |
| A git lookup, direct to GitHub and then through the broker (alternating) | 341 ms | 395 ms | 54 ms (16%) |

Times are medians of 10 runs unless stated. The desk's extra time is one HTTPS request over the tailnet: the connection, the check of who is calling, and starting the tool. The broker's is the same hop plus adding the key.

### How this was measured, and what was not

- **Setup:** Claude Code 2.1.329 with `claude-opus-5-5`, on an agent VM with nothing else installed in Claude Code; the desk and broker each on their own VM on a home network; November 2026.
- **Tokens** are the input counts Claude Code reports for a request (new and cached together), not an estimate from character counts.
- **Tool and git timings** were taken on a second agent VM that has the tool installed, so the same command could be run both ways.
- **Not measured:** commands that send a file to the desk (the file is uploaded first, so expect time in proportion to its size), MCP servers reached through the desk or broker, API routes other than git, other models (their token counts differ) and Codex.
- **One machine, one network.** Your numbers will differ. To get your own token figures, run this on the agent machine as the agent's user; it makes twelve small requests and does not touch your settings:

```sh
python3 agents/token-cost.py
```

```text
model: claude-opus-5-5   (median of 3 runs each)
                                 prompt tokens   added  response ms
nothing added                           38,308      +0          666
rules in CLAUDE.md                      40,791  +2,483          655
settings (sandbox and blocks)           41,640  +3,332          994
rules and settings                      44,128  +5,820        1,626
```

The response times in that output come from three runs each and swing widely; the ten-run figures in the table above are the ones to trust.
