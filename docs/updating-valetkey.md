# Updating valetkey

Both version commands use Python 3.9+ and its standard library. `valetkey-broker version` works offline. `valetkey-broker update` (available since 0.2.1) checks public GitHub tags anonymously and prints the newest stable version. It needs DNS, direct outbound HTTPS to `api.github.com` on TCP 443 and a working TLS trust store. It bypasses environment proxy settings. The check runs independently of Git, the broker and the vault. On the broker with `sudo`, `valetkey-broker update` goes further and installs the broker's own update; it shows what changes and asks first.

To update the version commands on an existing broker, vault, bellhop, bellhop-only agent or Proxmox
host, run `sudo ./install.sh update` from the reviewed valetkey checkout on that
machine. This updates the CLI without restarting services or provisioning guests. For a
user-local client, use `./install.sh update --prefix "$HOME/.local/bin"`. A full
client keeps its existing subcommands. Run `valetkey-broker version` for the installed valetkey CLI
and `valetkey-broker update` to check the latest public release. The vault server image and
bellhop services have their own update procedures.

From a reviewed release checkout on an existing Proxmox host, run
`bash deploy/proxmox/update-version-tools.sh VAULT_CT_ID BROKER_VM_ID BELLHOP_VM_ID`
as root to update the CLI on the host and all three guests. Use their numeric
IDs. The command checks each role first and verifies the installed CLI version
on each machine. The broker service has its own update command below.

- **macOS (Homebrew):** run `brew update` and then `brew upgrade valetkey-agent`,
  `brew upgrade valetkey-approver` or `brew upgrade valetkey-trusted-server` for the installed role.
- **Linux (apt):** run `sudo apt-get update && sudo apt-get install --only-upgrade valetkey`, then
  `valetkey-setup` to apply the new version to the machine's role. The broker updates with
  `sudo valetkey-broker update` instead.

Read [the migration procedure](secure-setup.md#migrate-an-existing-deployment-without-breaking-its-consumers)
first. Ordinary raw routes require explicit `trusted_value_nodes`. Prepare
the policy and verify replacement consumers before the change window. No deployed state is
changed by the source test suite.

```sh
git pull
sudo ./install.sh broker      # on the broker: replaces code and units, keeps /etc/valetkey-broker and logins
./install.sh client --agent   # on an agent, using its existing install prefix
```

The broker reports its installed version in `/usr/local/libexec/valetkey-broker/VERSION` and in `./install.sh doctor`.

## Updating to 0.5: names on disk

Before 0.5 the broker's files, users and services were named `relay` and the bellhop's were named
`desk`. From 0.5 they are named `valetkey-broker` and `bellhop`. Each machine's installer moves an existing
installation over the first time it runs. The commands are the usual ones. What is different:
**this update cannot be put back by `valetkey-broker rollback`.**

**Take a snapshot or a backup of each machine before you update it.** On Proxmox:
`qm snapshot <vmid> before-names`. If the services do not come back, return to the snapshot with
`qm rollback <vmid> before-names` and keep what the update printed.

| | Before 0.5 | From 0.5 |
|---|---|---|
| broker configuration | `/etc/relay/` | `/etc/valetkey-broker/` |
| broker services | `relay-front`, `relay-local`, `relay-infra`, `relay-entry`, `relay-approval`, `relay-cert.timer` | `valetkey-broker-front`, `valetkey-broker-local`, `valetkey-broker-infra`, `valetkey-broker-entry`, `valetkey-broker-approval`, `valetkey-broker-cert.timer` |
| broker users and their folders | `relay-<name>`, `/var/lib/relay-<name>` | `valetkey-broker-<name>`, `/var/lib/valetkey-broker-<name>` |
| broker code | `/usr/local/libexec/relay/` | `/usr/local/libexec/valetkey-broker/` |
| bellhop configuration | `/etc/bellhop/desk.toml`, section `[desk]` | `/etc/bellhop/bellhop.toml`, section `[bellhop]` |
| bellhop user and service | `desk`, `bellhop-desk` | `bellhop`, `bellhop` |
| bellhop address | `https://<name>/desk` | `https://<name>/bellhop` |

The move keeps the user ids, so every file keeps its owner. It keeps the vault logins, the routes
and their approvals, the authenticator pairing, the repository deploy key and the bellhop's tool
logins. The secret the broker's services share among themselves is made again.

**broker.** Run `sudo valetkey-broker update` as usual. It warns that this update needs a manual
step first: that step is the snapshot. It then checks that everything can be moved, stops the
services, moves the names, installs 0.5 and starts the services under their new names. Expect a
minute or two without the broker. Then run `sudo valetkey-broker check`.

- If a check fails before the move, nothing is changed and the old services keep running.
- `/etc/relay` and `/var/lib/relay/update` stay behind as links until the next update removes them.
- Every name valetkey uses on the broker starts with `valetkey-broker`. A user, folder, script
  or unit of your own called `broker` (a monitoring heartbeat, say) is never read or changed.
- 0.5.0 used plain `broker` names and was withdrawn the same day. A broker that installed it is
  not moved from there: restore the snapshot from before 0.5.0, then update.
- Settings you added beside a unit (a `relay-<name>.service.d` folder) move to the new unit's
  name. Check any path in them that says `relay`.

**bellhop.** Run its installer again: `bellhop/proxmox-bellhop-run.sh bellhop/install-bellhop.sh`
from the Proxmox host, or `sudo ./install.sh bellhop` on the machine. The old address, `.../desk`,
keeps answering beside the new one, so agent machines keep working. Then:

1. On each agent machine, run the installer again with `--bellhop-url https://<name>/bellhop`. If
   it wrote a Claude Code policy before, remove `/etc/claude-code/managed-settings.json` first so
   the policy is written with the new address.
2. On the bellhop, delete `/etc/bellhop/old-address` and run its installer once more. The old
   address stops answering.
3. The `desk` user's old home folder stays as a link to the new one, which is the `bellhop`
   folder beside it, for a tool that stored the old path. Remove the link once no file in the new
   folder mentions the old one.

**What the update does not change.** A bellhop that joined the tailnet as `desk` with `tag:desk`
keeps that name and tag; new installations use `bellhop` and `tag:bellhop`. To rename an existing
one: give the machine the new tag in the Tailscale admin console and in your tailnet policy, run
`sudo tailscale set --hostname=bellhop` on it, run its installer again (for a certificate in the
new name), and give each agent machine the new `--bellhop-url`. On trusted servers, a `fnox.toml`
that calls its provider `relay` keeps working; files generated from 0.5 call it `broker`.

## Updating the broker in one command

After a one-time setup the broker fetches its own code, so nothing has to be copied to it:

```sh
sudo valetkey-broker setup https://github.com/machinarii/valetkey.git   # once
sudo valetkey-broker update                                           # each update
```

Point `setup` at this repository or at your own fork. A public repository is read over https and
needs no key. For a private one, give the `git@github.com:<owner>/<repo>.git` address instead:
`setup` then makes a read-only deploy key (the private half is encrypted with `systemd-creds`),
prints the public half for the repository's Settings → Deploy keys (leave write access off), and
prints GitHub's SSH host key fingerprint to compare with the published one.

`install` fetches the branch, shows the commit ids, the commit titles and a GitHub link to the
diff, and asks for confirmation. Enter `y` to install or `n` to cancel. The updater already
on the broker handles this prompt, so an older installation may show its earlier wording.
Code from the fetched revision runs only after approval. The updater then checks
`/etc/valetkey-broker` with the new parser, keeps a copy of the installed code, runs `install.sh broker`,
and waits until the broker ports that listened before listen again. If they do not, it puts the
previous code back and says so. `sudo valetkey-broker rollback` does the same on request, for an
update that starts but misbehaves.

Run it only from the broker's console or LAN SSH, and review the diff first: anyone who can push
to the repository decides what that command installs. There is deliberately no remote or
automatic trigger.

`broker/CONFIG_EPOCH` is a number that changes whenever an update needs you to do something
first (edit `/etc/valetkey-broker`, change the vault or the tailnet). `install` warns when it differs from
the installed one; the steps are listed here, newest first:

- **2** (0.5): take a snapshot or backup of the broker first. Its names on disk move from
  `relay` to `broker`, and the updater cannot put that back. See
  [Updating to 0.5](#updating-to-05-names-on-disk).
- **1** (first numbered epoch): raw routes need `[security].trusted_value_nodes`, see the
  migration procedure above.
