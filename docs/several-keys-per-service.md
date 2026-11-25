# Several keys for one service

Some services need more than one stored key: one per job, each with only the rights that job
needs. GitHub is the usual example. This page explains why a broker route cannot replace that,
and how to split the keys.

## Why one broad key behind narrow routes is not enough

A route limits the host, the HTTP methods and the path prefixes. The key's own permissions limit
everything else. A route can narrow a key; it can never make a broad key safe.

1. **The broker cannot see what a request does.** It checks the method and the path, not the
   body. A `git push` sends its commits inside one request body; a GraphQL query and a GraphQL
   mutation are both `POST /graphql`. Only the service, reading the request with the key's
   permissions, tells a read from a write there.
2. **Reads and writes often share a method.** Git fetches with `POST .../git-upload-pack` and
   pushes with `POST .../git-receive-pack`. Allowing `POST` allows both, unless every
   repository's paths are listed one by one.
3. **A route is a filter you must get right; a key's scope is a guarantee.** A forgotten path,
   a prefix that is wider than meant, or a later edit opens everything the key can do. A
   read-only key stays read-only whatever the route says.
4. **A key can leak from somewhere other than the agent.** A wrong `upstream`, a compromised
   broker or vault, or a service that echoes headers back exposes the whole key. A narrow key
   limits what that leak costs.
5. **Every route that names an item gets that item's full power** within its own paths and
   methods. With one key per job, a route made for reading cannot borrow write rights.
6. **Jobs are revoked and rotated separately.** Revoking the key a pushing agent uses leaves
   read access, other accounts and CI working. The audit log names the item, so it also shows
   which job made a call.

## GitHub

Use fine-grained personal access tokens, limited to the repositories each job needs. A
fine-grained token belongs to one account or one organization, so each account needs its own.

| Job | Token permissions | Stored item | Route |
|---|---|---|---|
| Fetch and push code | Contents: read and write | `github-git-basic-acme`, base64 of `x-access-token:<token>` | `upstream = "https://github.com"`, `auth = "Basic {}"`, `GET`, `POST`, `paths = ["/acme/"]` |
| Read repositories, issues and pull requests over the API | Contents, Issues, Pull requests: read | `github-api-read-acme`, the bare token | `upstream = "https://api.github.com"`, `auth = "Bearer {}"`, `GET`, `paths = ["/repos/acme/"]` |
| Open and update pull requests | Pull requests: read and write | `github-api-pr-acme`, the bare token | as above, with `POST` and `PATCH` |
| Change CI workflows | Workflows | none: the owner does this | none |
| Settings, secrets, deleting repositories | Administration, Secrets | none: never through an agent | none |

Why these lines are drawn where they are:

- **Git and the API take different forms of the key.** Git logs in with `Basic` and the encoded
  value; the REST API takes the bare token with `Bearer`. The broker inserts the stored value as
  it is, so even the same token would need two items. See
  [Git through the broker](git-through-the-broker.md).
- **Pushing code is not the same as changing CI.** GitHub refuses a push that changes
  `.github/workflows/` unless the token has the Workflows permission. Keep it that way: a
  workflow runs with the repository's secrets, so an agent that can edit workflows can send
  those secrets anywhere. The owner adds or changes workflow files, in GitHub's web editor or
  with their own login.
- **Settings and secrets stay with the owner.** Administration can turn off branch protection
  or delete a repository; Secrets can replace a deploy secret. No agent job needs either.
- **Contents: write reaches every branch.** The broker cannot tell branches apart inside a push.
  Protect the branches that deploy (branch protection or rulesets on GitHub), so a push from an
  agent becomes a pull request a person merges.
- **GraphQL cannot be narrowed by path.** Everything goes to `POST /graphql`. If a job needs
  GraphQL, give it a read-only key and a route of its own.

## Other services that work the same way

- **Stripe:** restricted keys set read or write per resource, and test and live keys are
  separate. Give agents a restricted test key; a live key, if any, gets its own item and a
  `GET`-only route.
- **Cloudflare:** API tokens are limited per account, zone and permission. Use one token per job,
  for example DNS edit on one zone, rather than a global key.

When a service offers only one all-powerful key, the route is the only limit. Keep its methods
and paths as narrow as the job allows, or use the [desk](recipe-new-tool-with-credential.md#path-b-a-bellhop-entry)
with an allow list of subcommands.

## Naming the items

- Use `<service>-<form or rights>-<account>`, for example `github-api-read-acme`.
- **No item name may contain another `[api.*]` item's name.** fnox looks items up by name; if
  `github-api-acme` and `github-api-acme-pr` both exist, the first name matches two items and
  that org's read service does not start. Put the distinguishing word in the middle, as in the table.
- Write the job and the permissions in the request's `--purpose`, so the entry page tells the
  owner which token to create:

  ```sh
  valetkey-broker request github-api-read-acme --org local \
    --purpose "GitHub API, read-only: fine-grained token for acme, Contents/Issues/Pull requests: read"
  ```

## The cost

More keys means more to create, renew and rotate. Set an expiry on each token. When one
expires, the service answers `401` and the entry page marks the item as possibly expired; replace
it with `valetkey-broker request <item> --replace`. This is cheaper than a broad key leaking.
