# Develop Valetkey

## Project layout

```
valetkey/
├── install.sh                  vaultwarden / broker / client installer, doctor
├── VERSION, Makefile
├── relay/                      broker services (Python stdlib)
│   ├── relay_front.py          TLS front door, routes by path prefix
│   ├── relay_read.py           read relay (one per org): values, checks, backups, API forwarding
│   ├── relay_entry.py          entry service: requests, owner writes, web page
│   ├── entry-page.html
│   ├── relaylib/               bw client and cache, routes, whois identity, request store
│   ├── tools/                  gen-fnox-config, vault-info, valetkey-fnox-to-relay, vault-stub
│   └── tests/                  unit and policy tests with fake bw / tailscale / fnox
├── broker/
│   ├── deps.sh                 apt packages, Tailscale, bw, fnox
│   ├── setup.sh                users, code, configs, credentials, units
│   ├── bin/                    valetkey-relay login, valetkey-relay check, valetkey-relay routes, valetkey-relay update, relay-cert, with-bw-session
│   └── systemd/                relay-{front,local,infra,entry}.service, relay-cert.{service,timer}
├── client/                     relay, vault (fnox shim), valetkey-entry
├── bellhop/                    the desk: tools with their own login
│   ├── desk.py                 desk service: listed tools, install requests
│   ├── bellhop                 stand-in command for agent machines
│   ├── install-desk.sh         installs the desk behind relay_front
│   ├── desk-tools.sh           example tools with cautious allow lists
│   ├── sweep.py                agent-machine check for logins and new tools
│   ├── agent-rules.md          instruction section for AI agents
│   ├── proxmox-desk-*.sh       create the desk VM; run a script or open a shell in it
│   └── testvm/, fakes/         test machines and fixtures, synthetic logins only
├── config/                     routes / entry / vault.env examples
├── deploy/
│   ├── proxmox/                vaultwarden-ct.sh, broker-vm.sh (also builds the desk VM)
│   ├── vaultwarden/            in-container setup, admin token, close sign-ups
│   └── tailscale/              example ACL
├── agents/
│   ├── credential-rules.md     instruction section for AI agents
│   ├── claude-setup.py         asks what to block; updates Claude Code's CLAUDE.md and settings.json
│   └── token-cost.py           measures what the setup adds to a Claude Code session, in tokens and time
├── docs/images/                icon, how-it-works animation, and the script that draws the animation
└── docs/                       secure setup, architecture, threat model, fnox vault-provider notes, git through the broker,
                                keyless tool standard, recipe for adding a tool, several keys for one service
```

## Development

```sh
make test     # 230+ unit and policy tests (relay and bellhop); stdlib only, fakes for bw, tailscale and fnox
make lint     # bash -n on every script, shellcheck if installed, py_compile, no real tailnet names or home paths
make dist     # dist/valetkey-<version>.tar.gz
```

The tests cover the policy edges as well as the happy path: unknown callers, callers not in `nodes`, items outside `items`, wrong org, list attempts, values never appearing in logs or error bodies, name validation, near-duplicate flags, replace-with-backup, hash verification and lock-on-every-path. CI runs them on Python 3.11 to 3.13.

### Redrawing the README animation

`docs/images/how-it-works.gif` is drawn by `docs/images/make.py`. Only this script needs extra
tools; valetkey itself, its tests and its installers do not use them.

| Needs | Install |
|---|---|
| Python 3.9+ with [Pillow](https://pypi.org/project/pillow/) | `python3 -m pip install --user Pillow`, or `sudo apt-get install python3-pil` |
| [ffmpeg](https://ffmpeg.org) (builds the GIF palette and file) | `sudo apt-get install ffmpeg`, or `brew install ffmpeg` |
| DejaVu Sans fonts (falls back to macOS Arial or Pillow’s scalable default) | `sudo apt-get install fonts-dejavu-core` |

```sh
python3 docs/images/make.py    # rewrites docs/images/how-it-works.gif and prints its frame count and size
```

`PAUSE` controls the hold after each step. `FADE` sets the spotlight transition in seconds, and
`DIM` controls the contrast of boxes outside the spotlight. The script supersamples the artwork
and exports an 860 px-wide GIF at 25 fps with a 256-entry palette and light ordered dithering.
