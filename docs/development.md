# Develop valetkey

## Project layout

```
valetkey/
├── install.sh                  vaultwarden / broker / client installer, doctor
├── VERSION, Makefile
├── broker/                     the broker: services (Python stdlib) and its installer
│   ├── broker_front.py         TLS front door, routes by path prefix
│   ├── broker_read.py          read service (one per org): values, checks, backups, API forwarding
│   ├── broker_entry.py         entry service: requests, owner writes, web page
│   ├── entry-page.html
│   ├── brokerlib/              bw client and cache, routes, whois identity, request store
│   ├── tools/                  gen-fnox-config, vault-info, valetkey-fnox-to-broker, vault-stub
│   ├── tests/                  unit and policy tests with fake bw / tailscale / fnox
│   ├── deps.sh                 apt packages, Tailscale, bw, fnox
│   ├── setup.sh                users, code, configs, credentials, units
│   ├── migrate-names.sh        moves a broker from before 0.5 from the name relay to broker
│   ├── bin/                    valetkey-broker-login, valetkey-broker-check, valetkey-broker-routes, valetkey-broker-update (run as valetkey-broker <word>), valetkey-broker-cert, with-bw-session
│   └── systemd/                valetkey-broker-{front,local,infra,entry}.service, valetkey-broker-cert.{service,timer}
├── relay/relaylib/             only so that the updater of 0.4 and earlier can check the configuration
├── client/                     broker (valetkey-broker), vault (fnox shim), vault-entry (valetkey-entry)
├── bellhop/                    the bellhop: tools with their own login
│   ├── server.py               the bellhop's service: listed tools, install requests
│   ├── bellhop                 stand-in command for agent machines
│   ├── install-bellhop.sh      installs the bellhop behind broker_front
│   ├── bellhop-tools.sh        example tools with cautious allow lists
│   ├── sweep.py                agent-machine check for logins and new tools
│   ├── agent-rules.md          instruction section for AI agents
│   ├── proxmox-bellhop-*.sh    create the bellhop VM; run a script or open a shell in it
│   └── testvm/, fakes/         test machines and fixtures, synthetic logins only
├── config/                     routes / entry / vault.env examples
├── deploy/
│   ├── proxmox/                vaultwarden-ct.sh, broker-vm.sh (also builds the bellhop VM)
│   ├── vaultwarden/            in-container setup, admin token, close sign-ups
│   └── tailscale/              example ACL
├── agents/
│   ├── credential-rules.md     instruction section for AI agents
│   ├── claude-setup.py         asks what to block; updates Claude Code's CLAUDE.md and settings.json
│   └── token-cost.py           measures what the setup adds to a Claude Code session, in tokens and time
├── docs/images/                icon, the how-it-works animation (SVG) and its motion spec, page screenshots
└── docs/                       secure setup, architecture, threat model, fnox vault-provider notes, git through the broker,
                                keyless tool standard, recipe for adding a tool, several keys for one service
```

## Development

```sh
make test     # 230+ unit and policy tests (broker and bellhop); stdlib only, fakes for bw, tailscale and fnox
make lint     # bash -n on every script, shellcheck if installed, py_compile, no real tailnet names or home paths
make dist     # dist/valetkey-<version>.tar.gz
```

The tests cover the policy edges as well as the happy path: unknown callers, callers not in `nodes`, items outside `items`, wrong org, list attempts, values never appearing in logs or error bodies, name validation, near-duplicate flags, replace-with-backup, hash verification and lock-on-every-path. CI runs them on Python 3.11 to 3.13.

### Redrawing the README animation

`docs/images/how-it-works.svg` is one self-contained file: boxes and lines drawn by hand, and
motion as CSS keyframes, with no script in it. It plays inside an `<img>` tag, so GitHub shows it
animated, and readers who ask for reduced motion get the still diagram.

The motion is described in `docs/images/how-it-works.spec.json`: each moving dot with its path,
start time and label, and the four captions. The keyframes between the `@motion` markers in the
SVG are generated from it with the [animdiagram](https://github.com/praveenvijayan/animdiagram)
skill's scripts (Python standard library only; valetkey itself does not use them):

```sh
python3 ~/.agents/skills/animdiagram/scripts/timeline.py docs/images/how-it-works.spec.json \
  --inject docs/images/how-it-works.svg      # rewrites only what is between the markers
python3 ~/.agents/skills/animdiagram/scripts/check.py docs/images/how-it-works.svg
```

To move a box, edit its coordinates in the SVG and the matching `from`, `via`, `to` and `glow`
values in the spec, then run the two commands again. The checker gives two warnings, both deliberate. The title is not in
capitals because valetkey, bellhop, vault and broker are written in lower case. And the broker box
is 2 px taller than the 4 px grid gives, so the gap under its inner box equals the gap above its
name.

### Retaking the README screenshots

`docs/images/page-*.png` show the entry page, the page after a key is stored, and the route approval
page, with made-up services. `docs/images/pages.py` writes those three pages as HTML files from the
real page code, with no vault and no network:

```sh
python3 docs/images/pages.py /tmp/pages      # entry.html, stored.html, approve.html
```

Open each file in a browser window 390 px wide, once in light and once in dark mode, and save a
full-page screenshot as `page-<name>-<light|dark>.png`. The README picks the one that matches the
reader's theme.

The icon (`valetkey-icon.png`, `valetkey-icon-512.png`) was generated with ChatGPT's image tool
from the brief "a close-up robotic valet in a cap, holding a key beside its head", then cut out of
its background and resized.

## Releasing

1. Set the new version in `VERSION`, `CLIENT_VERSION` in `client/broker`, the `tag:` line of the three
   files in `Formula/`, the README status badge and the version line in `docs/security-boundaries.md`.
2. Run `make lint` and `make test`, commit, then tag and push: `git tag v<version> && git push origin main v<version>`.
3. **Homebrew** reads `Formula/` from `main`, so `brew upgrade` picks the release up once the tag exists.
4. **apt:** the `apt` workflow builds the package, signs the repository and publishes it to GitHub Pages
   when the tag is pushed. Its one-time setup (Pages source, the signing key secret) is described at the top of
   [`.github/workflows/apt.yml`](../.github/workflows/apt.yml); only the owner edits workflows, in
   GitHub's web editor (agents' git tokens have no Workflows permission). To build locally:
   `packaging/apt/build.sh` writes `dist/apt/`; set `APT_GPG_KEY=<fingerprint>` to sign it.

The apt package ships the installer and what it reads, never tests or docs, and only tracked files.
