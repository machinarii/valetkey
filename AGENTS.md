# Working on valetkey

Read [agents/credential-rules.md](agents/credential-rules.md) before credential-related work.
The agent default is broker-only API access. Never run agents on the broker, vault server,
owner entry device, trusted raw-value consumer, or their management host.

Use synthetic fixtures for all tests. Never read a deployed credential, unlock a vault, inspect
process environments, or contact live broker/vault services from an agent session. On Linux,
`bash relay/run-security-tests` isolates tests from the host home and external network.

Keep authorization in the broker, deny raw reads by default, preserve per-org isolation, and
test both denied requests and permitted workflows. `relay check` verifies storage/access;
upstream authentication is a separate status. See [docs/secure-setup.md](docs/secure-setup.md).
