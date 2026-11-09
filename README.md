# treat-agent-as-a-dev

Agent-driven setup that gives each developer's AI coding agent its
own GitHub App identity.

In short: an AI coding agent should commit, push, and open PRs as a
distinct `[bot]` principal — not as you. That gives auditable
attribution, prevents the human reviewer from accidentally
self-approving the agent's PR, and contains the blast radius if a
secret leaks. This repo ships a small CLI (`taaad`) that automates
the setup.

## How a developer uses this repo

Pin to a tag (see [Pinning](#pinning) — never `main`) and prompt
your agent:

> Follow the runbook at <https://github.com/de-otio/treat-agent-as-a-dev>
> at tag `<tag>`. Set up a GitHub App for engagement
> `<engagement>` using my GitHub user `<your-handle>`. Target repo
> `<owner>/<repo>`.

If the target repo lives in an organization (most common case),
the agent will pass `--org <owner>` to `taaad register` so the
App is owned by the org. If the target is in your personal
account, the App is user-owned by default. Apps are always
private (Operating Rule 8 in RUNBOOK.md) — there is no path that
makes them public.

The agent reads [RUNBOOK.md](RUNBOOK.md) and drives. You'll be
asked to do three things in your browser (create the App, install
it on the repo, approve the smoke-test PR); everything else is
automated. The agent will also recommend that you (or your
customer's repo admin) turn on branch protection — by design it
won't apply that itself, since repo settings on customer
engagements typically belong to the customer.

**One App, many repos.** Once registered, the App is reused: per
new repo it's just `taaad init` (sets a few keys in `.git/config`).
No files copied into the repo, no `bin/agent` per engagement.

**"Engagement"** here means the specific customer project or piece
of client work the bot is for. It's used as a short slug (e.g.
`acme`, `acme-q3`) to namespace this bot's name and keychain entry
so work for different customers stays cleanly separated. If you're
not doing client work, pick any short label that identifies the
project.

Total wall time on a fresh machine: about 10 minutes for the
first-time setup, < 1 minute per additional repo.

> **Heads-up for the smoke-test step.** On a brand-new private
> repo, GitHub's search backend takes a while to index the first
> PR — sometimes minutes, sometimes hours. While that's happening
> the repo's `/pulls` listing page can show "There aren't any
> open pull requests" *even though the tab badge counts the bot's
> PR*. The direct PR URL (`/<owner>/<repo>/pull/1`) and `gh api
> repos/<owner>/<repo>/pulls` both work fine — use those to find
> the PR for the approval step.

## Install

```sh
pipx install git+https://github.com/de-otio/treat-agent-as-a-dev@<tag>
taaad --version
```

If `pipx` isn't available, `pip install --user
git+https://github.com/...@<tag>` works too. Per-machine, not
per-engagement.

## `taaad mint` — a token for one repository

```sh
taaad mint --app example-bot --repo example-org/widgets \
           --permissions <read|write|contents-write> --json | consumer
           [--only PERM,... | --exclude PERM,...]   # read class only
```

All four options are required (`--json` is the only output format and
must be given). Mints an installation token whose body names exactly
one repository and one permission class, always intersected with what
the installation was granted:

| `--permissions`  | Token permissions                                                   |
|------------------|---------------------------------------------------------------------|
| `read`           | every permission the installation grants, each at `read` (see below) |
| `contents-write` | `contents: write`, `metadata: read` (refused if not granted)        |
| `write`          | the installation's granted permissions, unchanged                   |

`read` follows the installation: a permission a person grants on the
installation (code scanning, Actions, checks, …) is usable at `read` as
soon as it is accepted, with no taaad release. Permissions with no read
level (`workflows`, `profile`) are left out. Org-level permissions the installation
grants are included. `--only a,b` requests just those (each must be
granted, or nothing is minted); `--exclude a,b` leaves them out. If
GitHub refuses a level with HTTP 422, the error suggests `--exclude`.

`--repo` must be `<owner>/<repo>`; the owner must equal the
installation's account login (case-insensitive) or nothing is minted.
On success stdout carries exactly one JSON object:
`{token, expiresAt, installationAccount, installationId, appId,
repositories, permissions}`. If GitHub's response lists anything other
than the one requested repository, `mint` refuses and prints nothing.

`mint` refuses to run when stdout is a TTY (there is no `--force`);
the token only ever goes to a pipe. Exit codes, with stdout empty on
every non-zero exit: `0` token printed; `2` refused because stdout is a
TTY; `3` rate-limited by `taaad-signer`; `4` presence prompt cancelled
or denied; `1` anything else. Errors go to stderr, are passed
through a credential redactor, and never contain the token. Config is
located the same way as every other command: `TAAAD_CONFIG_DIR`, else
the platform config dir (`XDG_CONFIG_HOME` on Linux), so a caller that
must not be steerable should spawn it with a minimal environment.

## What's in this repo

- [`RUNBOOK.md`](RUNBOOK.md) — the playbook the agent follows
- [`SECURITY.md`](SECURITY.md) — secret-handling invariants and
  reporting
- [`src/taaad/`](src/taaad) — the CLI implementation
- [`docs/architecture.md`](docs/architecture.md) — component
  diagram and trust boundaries (for contributors and reviewers)

## Re-enrolling existing engagements

If you used the v0.4 shell-script setup and switched to v0.5, run
this once per existing engagement:

```sh
cd <engagement-repo>
taaad install <slug> --account <owner> --app-id <numeric-id>
taaad init --app <slug>
git rm bin/app-token.py bin/agent bin/agent-env.sh \
       bin/agent-env.ps1 bin/agent.ps1
rm -f .git/hooks/pre-commit
git commit -m "remove v0.4 agent scaffolding; migrated to taaad"
```

`--app-id` is the numeric ID at
`https://github.com/settings/apps/<slug>`. The keychain PEM is
reused as-is (same key naming as v0.4).

## Uninstalling

- Per-repo: `taaad uninit` reverses `taaad init`. Each git config
  key is unset only if its value matches what taaad wrote.
- Per-App: `taaad apps remove <slug>` after deleting the App on
  github.com (mandatory manual step — there is no API for it).
- Machine-wide: `taaad uninstall [--purge]`, then `pipx uninstall
  taaad`.

## Dotfiles sync

`<config-dir>/apps/*.toml` files are local-only and may name
customer organisations (in the `account` field). Exclude them from
any dotfiles sync (e.g. `chezmoi ignore`) — they're not portable.
PEMs are in the OS secret store, never in the config dir.

## Pinning

When pointing your agent at this repo, **pin to a tag or SHA**, not
to `main`. Tags matching `v*` are immutable (enforced via
repository ruleset and signed-tag requirement), so `v0.5` means the
same thing forever. The setup commands touch your keychain and
create GitHub Apps under your account; you don't want them tracking
a moving branch.

For belt-and-braces, pin to a commit SHA:
`pipx install git+https://github.com/de-otio/treat-agent-as-a-dev@<40-char-sha>`.
Verify with `git tag --verify v<x.y>` if you've imported the
project's signing key.

## What this repo isn't

A community open-source project. It's published publicly so devs
and agents can clone or fetch it by tag/SHA. External pull requests
will be politely closed. Issues are disabled. If you find a
security problem, see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
