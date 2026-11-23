# Updating valetkey

To update the version commands on an existing broker, vault, desk, desk-only agent or Proxmox
host, run `sudo ./install.sh update` from the reviewed Valetkey checkout on that
machine. This updates the CLI without restarting services or provisioning guests. For a
user-local client, use `./install.sh update --prefix "$HOME/.local/bin"`. A full
client keeps its existing subcommands. Run `relay version` for the installed Valetkey CLI
and `relay latest-version` to check the latest public release. The vault server image and
Bellhop services have their own update procedures.

From a reviewed release checkout on an existing Proxmox host, run
`bash deploy/proxmox/update-version-tools.sh VAULT_CT_ID BROKER_VM_ID DESK_VM_ID`
as root to update the CLI on the host and all three guests. Use their numeric
IDs. The command checks each role first and verifies the installed CLI version
on each machine. The broker service has its own update command below.

For Homebrew installs, run `brew update` and then `brew upgrade valetkey-agent`,
`brew upgrade valetkey-owner`, or `brew upgrade valetkey-consumer` for the installed role.

Read [the migration procedure](secure-setup.md#migrate-an-existing-deployment-without-breaking-its-consumers)
first. Ordinary raw routes require explicit `trusted_value_nodes`. Prepare
the policy and verify replacement consumers before the change window. No deployed state is
changed by the source test suite.

```sh
git pull
sudo ./install.sh broker      # on the broker: replaces code and units, keeps /etc/relay and logins
./install.sh client --agent   # on an agent, using its existing install prefix
```

The broker reports its installed version in `/usr/local/libexec/relay/VERSION` and in `./install.sh doctor`.

## Updating the broker in one command

After a one-time setup the broker fetches its own code, so nothing has to be copied to it:

```sh
sudo relay-update setup https://github.com/machinarii/valetkey.git   # once
sudo relay-update install                                            # each update
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
`/etc/relay` with the new parser, keeps a copy of the installed code, runs `install.sh broker`,
and waits until the relay ports that listened before listen again. If they do not, it puts the
previous code back and says so. `sudo relay-update rollback` does the same on request, for an
update that starts but misbehaves.

Run it only from the broker's console or LAN SSH, and review the diff first: anyone who can push
to the repository decides what that command installs. There is deliberately no remote or
automatic trigger.

`broker/CONFIG_EPOCH` is a number that changes whenever an update needs you to do something
first (edit `/etc/relay`, change the vault or the tailnet). `install` warns when it differs from
the installed one; the steps are listed here, newest first:

- **1** (first numbered epoch): raw routes need `[security].trusted_value_nodes`, see the
  migration procedure above.
