# Working on valetkey

Read [agents/credential-rules.md](agents/credential-rules.md) before credential-related work.
The agent default is broker-only API access. Never run agents on the broker, the desk, the vault
server, an owner entry device, a trusted raw-value consumer, or their management host.

For Claude Code’s own subscription, follow the [subscription authentication guidance](docs/secure-setup.md#claude-code-subscription-sign-in). Leave sign-in and session handling to the owner and Claude Code.

Use synthetic fixtures for all tests. Never read a deployed credential, unlock a vault, inspect
process environments, or contact live broker/vault services from an agent session. On Linux,
`bash relay/run-security-tests` isolates tests from the host home and external network.

`bellhop/` is the desk: it runs login-holding command-line tools for agent machines. Its rules
for agents are in [bellhop/agent-rules.md](bellhop/agent-rules.md); its tests are
`python3 -m unittest bellhop/test_bellhop.py`.

Keep authorization in the broker, deny raw reads by default, preserve per-org isolation, and
test both denied requests and permitted workflows. `relay check` verifies storage/access;
upstream authentication is a separate status. See [docs/secure-setup.md](docs/secure-setup.md).
