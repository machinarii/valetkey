# Git through the broker

Fetch and push over HTTPS with no token on the client. Git talks to the broker instead of the git
host; the broker checks which machine is calling, adds the credential itself and forwards only to
the repositories the route allows. An agent on that machine can use the route but can never read
the token.

This is an `[api.*]` route like any other. Do not use a `[value.*]` route for a git token: it hands
the raw token to the client, where every process of that user can read it.

The examples use GitHub, an account called `acme` and a client called `devbox`. Tested with
fnox 1.35.3 and Python 3.13 on the broker.

## Publishing GitHub Actions workflow changes

Keep the broker's Git credential scoped to the intended repositories with Contents write access.
Leave Workflows write access with the owner. An agent that can use a broker route can exercise
the permissions behind it even when it cannot read the token. A writable deploy key on the agent
machine would also grant repository write access. Personal SSH keys authenticate as the owner
and have no separate workflow scope. [GitHub token permissions](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents) · [Deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)

For an occasional edit, use GitHub's web editor on a separate approver device. Review the proposed
workflow, commit to a topic branch, and open a pull request. Keep that browser session and any
Git credential or SSH agent used for the push beyond the reach of agent tooling, including
remote browser debugging and forwarded authentication sockets.

For a change prepared locally, use the approver device to fetch the proposed branch and review
its diff against the target branch. After adding or editing the workflow, stage the intended
files and run `git diff --cached -- .github/workflows/` to check the exact YAML going into the
commit. Review the complete change as well, including local actions, Makefile targets, and
scripts the jobs execute. Then commit and push the reviewed change to the topic branch.

Complete that review **before the first push or pull request that can trigger the workflow**.
A branch push or PR can start a job before merge approval. Keep ordinary test jobs on a
read-only `GITHUB_TOKEN` with no deployment secrets. An agent that can edit scripts called by
an existing job can influence what runs even without permission to edit the workflow file.
[GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use)

## 1. Store the credential in the form the git host expects

Git logs in to GitHub over HTTPS with a `Basic` header. fnox swaps a placeholder for the stored
value as it is, so the item must already hold the encoded form: base64 of `x-access-token:`
followed by the token.

| Stored value | What GitHub answers |
|---|---|
| base64 of `x-access-token:<token>` | `200` |
| the token as it is | `400` with an empty body (not valid base64) |
| the token as it is, with `auth = "Bearer {}"` | `401`: GitHub does not accept `Bearer` for git |

Create a fine-grained token limited to the repositories the client should reach, with **Contents:
read and write** and nothing else beyond the automatic Metadata. Then, with the token on the
clipboard, encode it and check the result before entering it:

```sh
# macOS (use xclip or wl-copy/wl-paste on Linux)
printf 'x-access-token:%s' "$(pbpaste)" | base64 | tr -d '\n' | pbcopy
pbpaste | base64 -d | grep -q '^x-access-token:github_pat_' && echo "encoded" || echo "NOT encoded"
```

File the request and enter the encoded value:

```sh
relay request github-git-basic-acme --org local --purpose "git for acme's repositories from devbox"
# then: vault-entry <request id>   or the entry page
```

Use one token per account per job. Several machines can share it, because none of them holds it:
the route's `nodes` list says which machines may use it. [Several keys for one
service](several-keys-per-service.md) explains why, and which GitHub jobs get separate tokens.

## 2. Add the route

In `/etc/relay/routes.toml` on the broker:

```toml
[api.git-acme]
org      = "local"
item     = "github-git-basic-acme"
upstream = "https://github.com"
auth     = "Basic {}"
methods  = ["GET", "POST"]
paths    = ["/acme/"]
nodes    = ["devbox"]
```

```sh
sudo relay-check local             # the [api.git-acme] line must say ok
sudo relay-routes install local    # refuses to change anything if the item is missing or ambiguous
```

`paths` limits the route to one account. The repository list on the token is the real limit; adding
a repository later means editing the token, not the route.

## 3. Point git at the broker

On each client in `nodes`:

```sh
git config --global url."https://broker.<tailnet>.ts.net/local/git-acme/acme/".insteadOf "https://github.com/acme/"
git config --global http.https://broker.<tailnet>.ts.net/.postBuffer 52428800
git ls-remote https://github.com/acme/<repository>.git    # read-only test
```

The first line reroutes every repository of the account without touching any remote. The second
makes git state the upload size up front: the broker reads a request body only when it carries a
`Content-Length`, and a body is capped at 50 MB, so one push larger than that is refused.

To go back to a direct login on a client, remove the rewrite with
`git config --global --unset-all url."<same address>".insteadOf`.

## Rotating the token

1. `relay request github-git-basic-acme --org local --replace --purpose "…"` and enter the new
   value, encoded as above.
2. **Delete the backup item** (`github-git-basic-acme-backup-YYYYMMDD`) in Vaultwarden. fnox finds an
   `[api.*]` item by name, the backup's name contains that name, and with two matches fnox does not
   start, which stops every route of the org.
3. `sudo relay-routes install local`, so fnox reads the new value. It checks step 2 for you and
   changes nothing if a backup is still there.

A restart or a reboot of the broker while such a backup exists still stops that org's relay. Until
fnox can look items up by id, do not leave backups of `[api.*]` items in the vault.

## What the client can and cannot do

- It cannot read the token: the value exists only in Vaultwarden and in the fnox proxy's memory on
  the broker. The relay and the audit log see a placeholder.
- It can use the route for everything the token allows. The broker limits host, methods and the
  path prefix, but it cannot see inside a push, so it cannot tell one branch from another. If a
  branch deploys to production, protect it on the git host or keep pushes behind a human decision.
- Revoke a machine by removing it from `nodes` and running `relay-routes install`. Revoke the token
  on the git host.

## Troubleshooting

| Symptom on the client | Cause and fix |
|---|---|
| `502 backend unavailable` on every route of the org, and `relay check` fails too | The read relay is not running: fnox could not resolve one `[api.*]` item (missing, or its name is contained in another item's name). `sudo relay-check <org>` names it; fix the item, then `sudo relay-routes install <org>`. |
| `502 upstream error: SSLCertVerificationError` | A relay from before the Python 3.13 fix: fnox's proxy certificates have no Authority Key Identifier and the strict default check rejects them. Update the broker. |
| `400` with no message | The item holds the raw token, not the encoded form (section 1). |
| `401` | The token expired, was revoked or regenerated, or does not cover that repository. The entry page marks the item *may be expired*. |
| `403 not allowed` | The calling machine is not in the route's `nodes`. |
| `403 path not allowed`, `405` | The repository is outside the route's `paths`, or the method is not in `methods`. |
| A larger push fails, small ones work | `http.postBuffer` is not set for the broker's address on that client, or the push is over 50 MB. |
