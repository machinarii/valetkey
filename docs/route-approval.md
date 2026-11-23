# Pair an authenticator and approve an API route

Valetkey 0.1.2 adds two separate links. A broker administrator issues a **pairing link** for one approver device. An agent requests an **approval link** for one proposed route. The ordinary credential entry page still handles key storage.

## Pair once

1. On the trusted broker, install the 0.1.2 broker services from a reviewed checkout with `sudo ./install.sh broker`. The QR page uses the distribution's `qrencode` package. On Debian or Ubuntu, install it with `sudo apt-get install qrencode`. The broker installer also includes it with `--with-deps`. The version-tools install updates only the version CLI.
2. Choose the Tailscale machine name of an untagged approver device already listed in `/etc/relay/entry.toml` under `owner_nodes`. Keep agents on separate machines in `routes.toml` under `security.agent_nodes`.
3. At the broker console run the following, substituting that device's name for `laptop`.

   ```sh
   sudo relay-approval pair laptop
   ```

4. Open the printed HTTPS link on that approver device over Tailscale within ten minutes. In your authenticator choose **Add account → Scan QR code**. Scan the QR shown on the approver device and enter the six-digit code on that same page. **Authenticator paired** confirms enrollment. The QR contains the shared TOTP secret. Keep it out of chat, screenshots, synced notes and agent sessions.

The broker renders the QR locally. Opening another pairing link for the same device cancels its pending link. To replace a lost generator, repeat the console command and confirm the new generator. The previous generator remains active until confirmation. Pair each additional approver device separately. Keep the broker clock and authenticator clock synchronized.

## Approve a route

For a fixed bearer key already stored in the correct Vaultwarden collection, run this on the agent that will use it. Use the provider's documented HTTPS origin, methods and narrowest required paths.

```sh
relay request-route reports --org local --item reports-api-key \
  --upstream https://api.example.com --method GET --path /v1/reports
```

Open the printed approval link on a paired approver device within fifteen minutes. Verify the requesting machine, vault item, provider domain, methods and path prefixes, including their descendants. The provider receives the credential when the agent uses this route. Enter a fresh authenticator code and choose **Approve this route**. Wait for **Route active**. A code used for pairing or another approval cannot be reused.

For a missing key, first file `relay request` and use the broker's separate entry page or `vault-entry <id>` in the owner's private terminal. When the route is active, check storage and access with `relay check local reports-api-key`. Verify upstream authentication with a harmless allowed call. MCP servers with native OAuth use their provider's sign-in from an approved client outside the agent VM.

## Broker operations

The worker installs new bearer API grants for the requesting machine only. It stores grants in `/etc/relay/approved-routes.d` and leaves the administrator's TOML in place. Manual routing remains available for other authentication schemes. To revoke a web grant, the administrator removes its JSON file and runs `sudo relay-routes install <org>` and verifies denial from the affected agent. Restrict root access to this directory and the broker. Keep the vault, hypervisor, broker and approver device outside agent control.

If activation fails, check `systemctl status relay-approval.service`, `systemctl status relay-activate@local.service` (or `infra`) and `sudo relay-check <org>` at the trusted broker console. An interrupted installation is rolled back when the worker starts. If recovery fails, resolve the reported broker or vault issue, restart the worker, then file a fresh request. The worker's private database and its encrypted master credential together protect authenticator enrollments. Protect both when backing up the broker.
