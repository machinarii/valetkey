# API-billed models for Claude Code and Codex

Use a funded Anthropic, OpenAI or OpenRouter API account with the native CLI. The broker authorizes the calling machine and injects the provider key from Vaultwarden. The launcher gives the CLI a broker URL and a public placeholder for that invocation. Start the CLI normally to keep using its existing subscription setup.

| CLI | Provider setting | Account charged | Upstream inference endpoint |
|---|---|---|---|
| Claude Code | `anthropic` | Anthropic Console | `api.anthropic.com/v1/messages` |
| Codex | `openai` | OpenAI API project | `api.openai.com/v1/responses` |
| Claude Code | `openrouter` | OpenRouter | `openrouter.ai/api/v1/messages` |
| Codex | `openrouter` | OpenRouter | `openrouter.ai/api/v1/responses` |

Claude Code uses the Anthropic Messages protocol. OpenRouter recommends Anthropic models for Claude Code. Codex uses the streaming Responses protocol and needs a model that supports the tools your session uses. Choose a model available to your account. The broker forwards the ID you select. [Claude gateway compatibility](https://code.claude.com/docs/en/llm-gateway) · [OpenRouter for Claude Code](https://openrouter.ai/docs/guides/coding-agents/claude-code-integration) · [OpenRouter for Codex](https://openrouter.ai/docs/guides/coding-agents/codex-cli)

## Requirements

- **Agent machine.** Install the full Valetkey client (0.2.0+) with Python 3.9+. Configure its broker URL and allow HTTPS to the broker over Tailscale. The native `claude` or `codex` executable must be on the launcher’s `PATH`, including when T3 starts it. OpenRouter uses the same native CLI.
- **Broker.** Use Valetkey 0.2.0+ with its Vaultwarden and fnox integration. Allow outbound HTTPS on TCP 443 to the provider you select (`api.anthropic.com`, `api.openai.com` or `openrouter.ai`).
- **Provider account.** Enable API billing with the selected provider and choose a model that account can use. Store the API key through the approver workflow below.

## 1. Store the provider key

For OpenRouter, request an item by name from the agent:

```sh
valetkey-broker request openrouter-api-key --org local --purpose "API-billed model requests"
```

On a separate approver device, enter the provider API key in the broker entry page printed by the request, or run `valetkey-entry <request-id>` in your own unrecorded terminal. For a direct provider, use a dedicated item such as `anthropic-api-key` or `openai-api-key`. Subscription tokens belong to the subscription sign-in flow. [Subscription setup](secure-setup.md#claude-code-subscription-sign-in)

## 2. Authorize a model route

On the broker, add a table to `/etc/relay/routes.toml` for each CLI you want to authorize. Replace `devbox` with the agent's Tailscale machine name. These two routes share one OpenRouter item:

```toml
[model.router-claude]
org = "local"
nodes = ["devbox"]
item = "openrouter-api-key"
provider = "openrouter"
client = "claude"

[model.router-codex]
org = "local"
nodes = ["devbox"]
item = "openrouter-api-key"
provider = "openrouter"
client = "codex"
```

For direct billing, use the same table format with `provider = "anthropic"` and `client = "claude"`, or `provider = "openai"` and `client = "codex"`. Point `item` at that provider's key. Give each route a distinct name and keep the machine in `security.agent_nodes`.

Apply the local routes from the broker console:

```sh
sudo valetkey-broker routes check && sudo valetkey-broker routes install local
```

Model routes generate exact POST rules for inference. Claude routes also allow token counting at `/v1/messages/count_tokens`; provider support can vary. Direct OpenAI routes also allow `/v1/responses/compact`. Model discovery, account management and raw key reads are outside the grant. Any process on an authorized machine can spend credits through the route and select a model the provider key permits. Set model restrictions and spending limits with the provider where available. Prompts, tool definitions and tool results go to the selected provider, including OpenRouter and its serving provider when you choose OpenRouter.

## 3. Run and verify

On the authorized agent, check storage and route access by item name. Then make one short model turn to verify upstream authentication. The turn uses API credits.

```sh
valetkey-broker check local openrouter-api-key
valetkey-broker model claude router-claude anthropic/claude-sonnet-4.6 -p "Reply OK"
valetkey-broker model codex router-codex openai/gpt-5.4 exec "Reply OK"
```

For direct billing, if you named the routes `anthropic-dev` and `openai-dev`, the equivalent commands are

```sh
valetkey-broker model claude anthropic-dev claude-sonnet-4-6 -p "Reply OK"
valetkey-broker model codex openai-dev gpt-5.4 exec "Reply OK"
```

The model IDs above are examples. Use the provider's exact ID, including the organization prefix for OpenRouter. For an interactive session omit `-p "Reply OK"` or `exec "Reply OK"`. For a route in the infra organization, put `--org infra` immediately after `valetkey-broker model`. [OpenRouter model catalog](https://openrouter.ai/models) · [Anthropic API](https://platform.claude.com/docs/en/api/overview) · [Codex provider configuration](https://developers.openai.com/codex/config-reference)

A successful `valetkey-broker check` verifies item presence and machine permission. A model reply verifies the provider accepted the request. For a 401 or 403, check the route's machine list, provider and item before replacing a key. A 402 or 429 can indicate insufficient credits or a provider limit. Codex may show fallback metadata for a model outside its bundled catalog. In Claude Code, `/status` should show the broker base URL. Check for conflicting provider settings if it shows a different URL.

For a host such as T3 that takes a CLI binary path, the owner can point it at a dedicated executable wrapper for the API-billed instance. Use the full path to the installed `valetkey-broker` if the service has a limited PATH. The native `claude` or `codex` executable must also be on that PATH. For example:

```sh
#!/bin/sh
exec valetkey-broker model codex router-codex openai/gpt-5.4 "$@"
```

Keep your subscription launcher as a separate selection. Neither configuring a route nor reading this guide switches a running session.
