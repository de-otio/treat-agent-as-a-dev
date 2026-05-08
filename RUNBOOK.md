# RUNBOOK — set up the bot identity for an AI coding agent

You are an AI coding agent setting up a GitHub App as your own
identity, on behalf of a developer. End state: PRs in the target
repo show `<APP_SLUG>[bot]` as author; the developer reviews and
approves; the bot can't self-approve (GitHub Apps cannot approve
PRs).

This runbook drives the `taaad` CLI. Two flows live here:

- [First-time machine setup](#first-time-machine-setup) — one App,
  one keychain entry, one CLI install. Done once per developer.
- [Per-repo setup](#per-repo-setup) — adopt the App in an
  engagement repo. Done once per repo. With one App installed
  across many repos in an org, this is just `taaad init` per repo.

## Operating rules (do not violate)

1. **PEM bytes never leave a sanctioned consumer.** The PEM lives
   only in the OS secret store and (transiently) in the memory of
   the `taaad` process while it mints a JWT. The following are
   prohibited regardless of intent (debugging, testing, "just
   checking the format"):
   - Printing the PEM to stdout / stderr / log / file.
   - Piping it into `head`, `cat`, `grep`, `tail`, `wc -l`, `xxd`,
     `od`, or anything that displays content.
   - Calling `security find-generic-password ... -g` (prints
     keychain attributes including the password).
   - Echoing any **encoded** form of the PEM (ASCII hex, base64,
     etc.) — encodings of secrets are still secrets.
   - Pasting PEM bytes into a chat / web tool / external service.

   Sanctioned inspections:
   - **Presence:** `taaad apps show <slug>` reports
     `keychain_pres.: yes/NO`.
   - **End-to-end smoke:** `taaad doctor --app <slug>` mints a
     token end-to-end and reports success/shape, never the PEM.

   If a debugger instinct kicks in to look at the PEM "just to
   check," **stop**. The format is documented (PEM is PEM); the
   answer to any malformed-PEM bug is to fix the storage/retrieval
   in `taaad`'s `secrets.py` or `github.py`, never to inspect a
   specific developer's PEM.

2. **No secret in commits.** App ID, install ID, slug, and the
   bot's `users.noreply.github.com` email are not secrets and may
   be committed. The PEM, the webhook secret, and any installation
   token are secrets and must not be.

3. **`GH_TOKEN` lives in environment only.** Never write it to disk.
   Never echo it. The token shape (`ghs_…`) is itself a marker —
   do not paste tokens into chat or logs. `GIT_TRACE*` /
   `GIT_CURL_VERBOSE` must remain unset; `taaad doctor` enforces.

4. **Stop on failure.** Surface the exact error and which step
   failed. Let the developer choose retry vs. manual fallback. Do
   not silently retry or improvise around blocked operations.

5. **Pause at human checkpoints.** Three steps require a click in
   the developer's browser. Each is marked `🛑 HUMAN CHECKPOINT`.

6. **Ask, don't guess.** If a required input is missing, ask. Do
   not invent values for `<engagement>`, `<owner>/<repo>`,
   `<gh-user>`, or `<bot-name>`.

7. **Use `taaad` subcommands; do not re-implement them.** The PEM
   safety properties depend on the consumer being `taaad`. Never
   call `keyring`, `security`, `secret-tool`, or
   `find-generic-password` directly to read or write the PEM.

## Inputs

**Glossary.** "Engagement" means the specific customer project or
piece of client work this bot is for. It's a short lowercase slug
(e.g. `acme`, `acme-q3`) that namespaces the bot's name and
keychain entry, so work for different customers stays cleanly
separated. If the developer isn't doing client work, treat it as a
project label.

| Variable          | Example            | Notes                                          |
|-------------------|--------------------|------------------------------------------------|
| `<engagement>`    | `acme`             | customer / project slug; lowercase, no spaces  |
| `<owner>/<repo>`  | `acme-co/widgets`  | target repo on github.com                      |
| `<gh-user>`       | `alice-jones`      | the developer's GitHub handle                  |
| `<bot-name>`      | `acme-alice-agent` | optional; overrides default `<engagement>-<gh-user>-bot` slug |

The default bot name is `<engagement>-<gh-user>-bot`. If the
developer wants a different name, capture it as `<bot-name>` and
pass it via `--name` in Step 1. GitHub returns the canonical slug
after creation; everything downstream uses that.

## Pre-flight

Run all of these. If any fails, stop and report what's missing.

```sh
gh auth status                           # human gh auth must be active
gh api repos/<owner>/<repo> --jq .name   # repo reachable
git rev-parse --show-toplevel            # in a git working tree (per-repo step only)
taaad --version                          # CLI installed
taaad doctor                             # backend / path / env clean
```

`taaad doctor` checks: keyring backend isn't a plaintext fallback,
the binary path is owned by the user and not world-writable, and
no `GIT_TRACE*` / `GIT_CURL_VERBOSE` env vars are set. Any
**[FAIL]** must be resolved before continuing.

The developer's existing `gh auth` is the **human** identity and is
what will be used for the smoke-test approval (Step 7). It must
remain logged in throughout.

# First-time machine setup

These steps create the App on github.com, capture the install ID,
and record everything in `taaad`'s config. Done once per developer.

## Step 0 — Install `taaad`

```sh
pipx install git+https://github.com/de-otio/treat-agent-as-a-dev@<tag>
```

Always pin to a tag (see [README.md → Pinning](README.md#pinning)).
Verify: `taaad --version`.

## Step 1 — Register the App via manifest flow 🛑

```sh
# Default name (`<engagement>-<gh-user>-bot`):
taaad register --engagement <engagement> --gh-user <gh-user>

# Or with a custom name:
taaad register --name <bot-name> --gh-user <gh-user>
```

The command:

1. Generates a manifest with the App's name, redirect URL, and the
   minimum permissions (`contents:write`, `pull_requests:write`,
   `metadata:read`).
2. Starts a localhost HTTP server on a free port. The server
   validates `Host` and `Origin` headers and rejects
   `Sec-Fetch-Site: cross-site`.
3. Opens a browser to a self-hosted form that auto-POSTs the
   manifest to GitHub.
4. **🛑 HUMAN CHECKPOINT 1.** The developer reviews the permissions
   on the GitHub page and clicks **Create GitHub App**. They must
   be logged in as `<gh-user>` — not the customer's account.
5. Captures the redirect, exchanges the code via
   `POST /app-manifests/{code}/conversions`.
6. Writes the PEM directly to the OS secret store (`keyring`)
   under key `github-app-<APP_SLUG>-pem`, where `APP_SLUG` is the
   slug GitHub returned (may differ from the requested name if
   GitHub appended a disambiguator).
7. On macOS, restricts the keychain item ACL to the `taaad` binary
   so other processes prompt the user before reading the PEM.
8. Writes `<config-dir>/apps/<APP_SLUG>.toml` with App ID, slug,
   and keychain key. No secrets in this file.
9. Prints `slug`, `app_id`, install URL, and the next-step command.

If the command times out (5 min idle), re-run it.

## Step 2 — Verify the PEM is stored

```sh
taaad apps show <APP_SLUG>
```

`keychain_pres.: yes` confirms the PEM is reachable. The value is
not printed.

If `NO`: ask the developer to manually re-run `taaad register`
(the manifest flow may have failed mid-write).

## Step 3 — Install on the target repo 🛑

The output of Step 1 prints an install URL. Open it:

```sh
open "https://github.com/apps/<APP_SLUG>/installations/new"   # macOS
xdg-open "https://github.com/apps/<APP_SLUG>/installations/new"  # Linux
Start-Process "https://github.com/apps/<APP_SLUG>/installations/new"  # Windows
```

**🛑 HUMAN CHECKPOINT 2.** The developer sees "Install
`<app-name>`", selects **Only select repositories**, picks
`<owner>/<repo>`, clicks **Install**.

Then capture the install ID:

```sh
taaad install <APP_SLUG> --account <owner>
```

This polls for up to 3 minutes. It mints an App JWT in `taaad`'s
process memory using the keychain PEM, calls
`/app/installations`, and writes the matching install ID into
`apps/<APP_SLUG>.toml`. Idempotent: re-runs on an unchanged
install are no-ops.

If the developer installed on a different repo or under a different
account (e.g. an org instead of their personal account), the poll
times out. Ask them to confirm what they selected.

## Step 4 — Recommend branch protection (advisory)

**The agent does not configure branch protection.** Repo settings
on customer projects are usually owned by the customer's GitHub
admin (an org owner, security team, or release manager). Even when
the developer has admin rights, applying protection
programmatically can collide with existing org policy or invalidate
compliance posture set by the customer. So this step is purely
advisory.

Print this recommendation to the developer verbatim:

> ⚠️ **Action required.** The bot identity is most useful when the
> default branch is protected so the bot can't push directly and
> the human reviewer must approve. Before running Step 7, please
> apply these rules to `<owner>/<repo>`'s default branch — or ask
> the repo admin / customer-side admin to apply them:
>
> - Require pull request reviews (≥ 1 approving review)
> - Dismiss stale reviews on new commits
> - Require approval of the most recent push
> - Disallow force-pushes
> - Disallow branch deletion
>
> On free GitHub plans, private repos can't use legacy "Branch
> protection rules" but **Repository rulesets** (Settings → Rules →
> Rulesets) provide the same controls.

Do **not** call `gh api -X PUT .../protection` or
`POST .../rulesets`. Do **not** infer the developer's admin status.
Do **not** wait for confirmation that the rules were applied —
record the recommendation and continue.

# Per-repo setup

For one App across multiple repos in an org, this is the **only**
section that runs more than once. Steps 0–3 happen once per
machine; this happens once per repo.

## Step 5 — `taaad init`

```sh
cd <engagement-repo>
taaad init
```

What this does (writes only `.git/config`, no tracked files):

- If `origin` is `git@github.com:…`, rewrites it to `https://`.
  (Git only invokes credential helpers for HTTPS.)
- Prompts the developer to pick an app, defaulting to the one whose
  `account` matches the origin owner. Or pre-select with
  `taaad init --app <slug>`.
- Sets `bot.app`, `user.name`, `user.email`, `credential.helper`
  in the **local** repo config. The credential helper is the
  absolute path to the `taaad` binary, validated for ownership and
  mode at write time.
- Records the repo path in `<config-dir>/used-by-<slug>.txt` so
  later `taaad apps remove` can offer to clean up matched repos.

Then optionally:

```sh
taaad hooks install
```

Sets `core.hooksPath` to taaad's shared hooks dir, after refusing
to clobber any existing value (including husky / pre-commit
framework). Skip this on repos that already manage their own
hooks; the bot identity check will still run on every push because
the credential helper validates it.

## Step 6 — (no manual git-config required)

`taaad init` (Step 5) already wrote `user.name`, `user.email`, and
`credential.helper`. No further per-repo config is needed.
Verify:

```sh
taaad doctor
```

Should report `[ok]` lines for the repo's local config.

## Step 7 — Smoke test 🛑

```sh
taaad agent gh auth status
taaad agent sh -c '
  set -e
  git checkout -b agent-smoke-test
  echo smoke > smoke.txt
  git add smoke.txt
  git commit -m "smoke test"
  git push -u origin agent-smoke-test
  gh pr create --title "smoke" --body "smoke test"
'
```

(Note: `sh` is not on the default `agent.allowed_commands`
allowlist; if you need it for this exercise, add it temporarily to
`<config-dir>/config.toml` under `[agent].allowed_commands`. For
production use, run `git`/`gh` directly via `taaad agent`.)

Confirm the PR author is the bot. The gh CLI uses `app/<slug>` as
the author filter for App-authored PRs, **not** `<slug>[bot]`:

```sh
PR_NUM="$(taaad agent gh pr list \
  --author "app/<APP_SLUG>" \
  --state open --json number --jq '.[0].number')"

taaad agent gh pr view "$PR_NUM" --json author --jq .author.login
# expected: app/<APP_SLUG>
```

If `gh pr list` returns nothing on a brand-new private repo, see
the search-index note below — the PR exists, search just hasn't
indexed it. Fall back to:

```sh
PR_NUM="$(taaad agent gh api repos/<owner>/<repo>/pulls \
            --jq '.[] | select(.head.ref=="agent-smoke-test") | .number')"
```

**🛑 HUMAN CHECKPOINT 3.** Tell the developer to run, in **their**
shell (not the bot's, with `GH_TOKEN` unset):

```sh
GH_TOKEN= gh pr review <PR_NUM> --approve --repo <owner>/<repo>
GH_TOKEN= gh pr merge  <PR_NUM> --squash --delete-branch --repo <owner>/<repo>
```

Or via browser: open
`https://github.com/<owner>/<repo>/pull/<PR_NUM>` directly and
click **Approve** + **Merge pull request**.

Wait for the developer to confirm the merge succeeded. If they hit
"you cannot approve your own pull request", the bot identity didn't
take in Step 5 — re-check `git config user.email` in this working
tree and re-run `taaad doctor`.

> ⚠️ **Heads-up on new private repos: search indexing lag.** GitHub
> indexes a private repo's issues and PRs into its search backend
> on first activity, with a delay that's usually minutes but can
> stretch to hours. Until indexing catches up, `gh pr list` returns
> empty; the direct PR URL and `gh api repos/.../pulls` work fine.

## Step 8 — Hand off

Report to the developer:

```
✅ Setup complete.

App:           <APP_SLUG>           (id <APP_ID>)
Installed on:  <owner>/<repo>       (install id <INSTALL_ID>)
PEM:           OS secret store, key github-app-<APP_SLUG>-pem
Smoke PR:      #<PR_NUM> merged at <SHA>

Day-to-day: launch your agent CLI via `taaad agent <cli>` (e.g.
`taaad agent claude`, `taaad agent codex`). The launcher mints a
fresh installation token per `git push` (via the credential helper)
and exports the bot identity for the agent process.

`taaad doctor` is a fast read-only sanity check; run it any time
something feels off.
```

If anything didn't go to plan, list the failures with the matching
step number; let the developer decide retry vs. manual.

## Step 9 — Rotate the PEM (when needed)

Rotate **whenever** the PEM may have been exposed: agent or human
echoed it (in any encoding), it landed in a log / transcript /
chat, the workstation was lost or shared, or it's just been a long
time. Rotation is cheap; treat it as the default response to any
doubt.

```
Rotation flow (3 minutes, two human checkpoints):

1. The agent should NOT generate or fetch the new PEM itself —
   GitHub's UI is the only path that produces one for a personal
   App, and routing it via the agent risks re-exposing it. Tell
   the developer to:

     a. Open https://github.com/settings/apps/<APP_SLUG>
        → "Private keys" → "Generate a private key".
     b. GitHub downloads <APP_SLUG>.YYYY-MM-DD.private-key.pem to
        ~/Downloads/. Do not open it in a text editor.

2. 🛑 HUMAN CHECKPOINT — wait for the developer to confirm the
   download landed.

3. Install the new PEM via `taaad rotate`. The CLI streams the file
   into the keychain, mints a token end-to-end to verify, and only
   then shreds the file. The agent does not read the bytes.

       taaad rotate <APP_SLUG> \
         --pem-file ~/Downloads/<APP_SLUG>.YYYY-MM-DD.private-key.pem

4. 🛑 HUMAN CHECKPOINT — tell the developer to revoke the OLD
   private key in the App settings page ("Private keys" → delete
   the old key by date). This invalidates any token already
   minted from the old PEM.
```

## Step 10 — Teardown

Three uninstall modes, smallest first.

### Per-repo unlink

When the developer wraps a single engagement repo but keeps the
machine-wide install:

```sh
cd <engagement-repo>
taaad uninit
```

Each git config key (`bot.app`, `user.name`, `user.email`,
`credential.helper`, `core.hooksPath`) is unset only if the value
matches what `taaad init` would have written, otherwise kept. Pass
`--force` to unset regardless. Keychain entries are not touched.

### Engagement-end (delete one App)

```sh
taaad apps remove <APP_SLUG>
```

The first run prints the GitHub-side cleanup and exits with code
3. Re-run with `--ack-github-cleanup` once you've:

1. https://github.com/settings/installations → uninstall the App
   from the org (revokes any tokens already minted).
2. **🛑 HUMAN CHECKPOINT.** https://github.com/settings/apps/<APP_SLUG>/advanced
   → Delete GitHub App. GitHub does not expose an App-deletion
   API for user-owned Apps; this is mandatory and manual.

After acknowledging, the keychain entry, the `apps/<slug>.toml`,
and the `used-by-<slug>.txt` registry are all removed.

### Machine teardown

```sh
taaad uninstall            # remove config dir; leave keychain entries
taaad uninstall --purge    # also delete keychain entries

# After taaad uninstall, the binary itself is removed via:
pipx uninstall taaad
```

`--purge` requires `--ack-github-cleanup` if any Apps are
registered, since deleting the keychain without deleting the
github.com App leaves orphan installs no one can rotate.

# Diagnostics

`taaad doctor` is the entry point. Common signals:

| Output                                                      | Likely cause                       | Fix                          |
|-------------------------------------------------------------|------------------------------------|------------------------------|
| `keyring backend ... unsafe`                                | Linux session keyring not running  | `dbus-launch`/`gnome-keyring-daemon`, or install `pass` |
| `taaad path component … is group/world writable`            | `taaad` installed under `/tmp` etc.| Reinstall via `pipx` to `~/.local/bin/` |
| `token-leak env vars set: GIT_TRACE`                        | leftover debug env                 | `unset GIT_TRACE GIT_TRACE_CURL GIT_CURL_VERBOSE` |
| `PEM missing at keychain key …`                             | rotation half-done, or new machine | re-run `taaad rotate` or `taaad register` |
| `core.hooksPath set in multiple sources`                    | husky / pre-commit + taaad collide | `taaad hooks uninstall`; pick one tool |
| `installation token mints successfully`                     | end-to-end OK                      | (none)                       |

# Fallbacks

- **No browser available (SSH / headless box).** `taaad register`
  needs a browser. Fall back: print the manifest URL and ask the
  developer to open it on a workstation, then paste the redirect
  URL with `?code=…&state=…` back; we exchange the code via
  `POST /app-manifests/{code}/conversions`. (Not currently a
  built-in flag — this is operator-driven.)

- **Developer's `gh` is logged in as the bot when human action is
  needed.** Have them open a fresh terminal with `GH_TOKEN`
  unset, or run `gh auth switch`.

- **`pipx install` fails with `ModuleNotFoundError: jwt`.** That's
  pyjwt missing — `pipx install --force` from the pinned tag
  reinstalls dependencies. Or check `pipx runpip taaad install
  pyjwt`.

- **PEM suspect.** Don't investigate by reading bytes. Rotate
  (Step 9).

- **Anything else.** Report it. Let the developer decide retry vs.
  manual.
