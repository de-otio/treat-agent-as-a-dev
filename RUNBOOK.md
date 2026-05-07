# RUNBOOK — set up a GitHub App as the agent's identity

You are an AI coding agent setting up a GitHub App on behalf of a
developer. End state: PRs in the developer's target repo show
`<APP_SLUG>[bot]` as author; the developer reviews and approves; the
bot can't self-approve (GitHub Apps cannot approve PRs).

## How to use this repo

Pick whichever fits your runtime:

- **Clone.** `git clone --branch <tag> https://github.com/de-otio/treat-agent-as-a-dev`
  and follow paths relative to the clone.
- **Fetch.** Read individual files via raw URL:
  `https://raw.githubusercontent.com/de-otio/treat-agent-as-a-dev/<tag>/<path>`.

Either way, refer to scripts and templates by their paths in this repo
(`scripts/manifest-flow.py`, `templates/bin/agent-env.sh`, …). Always
operate at a pinned tag, never `main` (see [README.md → Pinning](README.md#pinning)).

## Operating rules (do not violate)

1. **The PEM bytes never leave a sanctioned consumer.** The PEM is
   captured in memory from the conversion API response by
   `scripts/manifest-flow.py` and written directly to the OS secret
   store. From that moment on, the only places its bytes may appear
   are: (a) memory of `bin/app-token.py` / `scripts/list-installs.py`
   while they mint a JWT, and (b) the OS secret store itself. The
   following are prohibited regardless of intent (debugging, testing,
   confirming format, etc.):
   - Printing PEM to stdout / stderr / log / file.
   - Piping PEM into `head`, `cat`, `grep`, `tail`, `wc -l`,
     `xxd`, `od`, or anything that displays content.
   - Calling `security find-generic-password ... -g` (prints
     keychain attributes including the password).
   - Echoing any **encoded form** of the PEM (ASCII hex, base64,
     etc.) — encodings of secrets are still secrets.
   - Pasting PEM bytes into a chat / web tool / external service.

   Sanctioned inspections that do **not** expose bytes:
   - **Presence:** `security find-generic-password -s "$KEY" >/dev/null && echo present`
     (the `-w` form is fine when its output is **discarded**).
   - **Length:** `security find-generic-password -s "$KEY" -w | wc -c`
     (returns one integer; bytes pass through the pipe unread by you).
   - **End-to-end smoke:** mint a token via `bin/app-token.py` and
     check the response shape, not the PEM input.

   If a debugger instinct kicks in to look at the PEM "just to
   check the format," **stop**. The format is documented (PEM is
   PEM) and the answer to any malformed-PEM bug is to fix the
   storage/retrieval layer in `scripts/list-installs.py` /
   `templates/bin/app-token.py`, never to inspect a specific
   developer's PEM.
2. **No secret in commits.** App ID, Installation ID, slug, and the
   bot's `users.noreply.github.com` email are not secrets and may be
   committed. The PEM, the webhook secret, and any installation token
   are secrets and must not be.
3. **`GH_TOKEN` lives in environment only.** Never write it to disk.
   Never echo it. The token shape (`ghs_…`) is itself a marker — do
   not paste tokens into chat or logs.
4. **Stop on failure.** Surface the exact error and which step
   failed. Let the developer choose retry vs. manual fallback. Do
   not silently retry or improvise around blocked operations.
5. **Pause at human checkpoints.** Three steps require a click in the
   developer's browser. Each is marked `🛑 HUMAN CHECKPOINT`. Do not
   proceed past one until the developer has confirmed.
6. **Ask, don't guess.** If a required input is missing, ask. Do not
   invent values for `<engagement>`, `<owner>/<repo>`, `<gh-user>`,
   or `<bot-name>`.

## Inputs

**Glossary.** "Engagement" means the specific customer project or
piece of client work this bot is for. It's a short lowercase slug
(e.g. `acme`, `acme-q3`) that namespaces the bot's name, the
keychain entry holding its PEM, and the repo it operates on, so that
work for different customers stays cleanly separated. If the
developer isn't doing client work, treat it as a project label.

| Variable          | Example            | Notes                                          |
|-------------------|--------------------|------------------------------------------------|
| `<engagement>`    | `acme`             | customer / project slug; lowercase, no spaces  |
| `<owner>/<repo>`  | `acme-co/widgets`  | target repo on github.com                      |
| `<gh-user>`       | `alice-jones`      | the developer's GitHub handle                  |
| `<bot-name>`      | `acme-alice-agent` | optional; overrides default `<engagement>-<gh-user>-bot` slug |

The default bot name is `<engagement>-<gh-user>-bot`. If the
developer wants a different name, capture it as `<bot-name>` and
pass it via `--name` in Step 1. Either way, GitHub returns the
canonical slug after creation as `APP_SLUG`; everything downstream
uses that.

Detect host OS once and reuse:

```sh
python3 -c "import platform; print(platform.system())"
# → Darwin | Linux | Windows
```

## Pre-flight

Run all of these. If any fails, stop and report what's missing.

```sh
gh auth status                              # human gh auth must be active
gh api repos/<owner>/<repo> --jq .name      # repo reachable
git rev-parse --show-toplevel               # in a git working tree
python3 -c "import jwt, requests"           # pyjwt + requests installed
```

The developer's existing `gh auth` is the **human** identity and is
what will be used for the smoke-test approval (Step 7). It must
remain logged in throughout.

## Step 1 — Register the App via manifest flow 🛑

Run [`scripts/manifest-flow.py`](scripts/manifest-flow.py). With the
default name (`<engagement>-<gh-user>-bot`):

```sh
eval "$(python3 scripts/manifest-flow.py \
  --engagement <engagement> \
  --gh-user <gh-user>)"
echo "$APP_ID $APP_SLUG $APP_INSTALL_URL"
```

Or with a custom bot name:

```sh
eval "$(python3 scripts/manifest-flow.py \
  --name <bot-name> \
  --gh-user <gh-user>)"
echo "$APP_ID $APP_SLUG $APP_INSTALL_URL"
```

The script:

1. Generates a manifest with the App's name, redirect URL, and the
   minimum permissions (`contents:write`, `pull_requests:write`,
   `metadata:read`).
2. Starts a localhost HTTP server on a free port.
3. Opens a browser to a self-hosted form that auto-POSTs the manifest
   to GitHub.
4. **🛑 HUMAN CHECKPOINT 1.** The developer reviews the permissions
   on the GitHub page and clicks **Create GitHub App**. They must be
   logged in as `<gh-user>` — not the customer's account.
5. Captures the redirect, exchanges the code via
   `POST /app-manifests/{code}/conversions`.
6. Writes the PEM directly to the OS secret store under key
   `github-app-${APP_SLUG}-pem`, where `APP_SLUG` is the slug
   GitHub returned (may differ from the requested name if GitHub
   appended a disambiguator).
7. Prints `APP_ID`, `APP_SLUG`, `APP_INSTALL_URL` on stdout in
   shell-eval format. No secrets printed.

If the script times out (5 min idle), re-run it. If the developer
doesn't have a desktop browser available, see
[Fallbacks](#fallbacks).

## Step 2 — Verify the PEM is stored

`scripts/manifest-flow.py` already wrote it. Confirm presence without
printing the value:

```sh
# macOS
security find-generic-password -s "github-app-${APP_SLUG}-pem" >/dev/null \
  && echo "PEM present"

# Linux (libsecret)
secret-tool lookup service "github-app-${APP_SLUG}-pem" >/dev/null \
  && echo "PEM present"

# Linux (pass)
pass show "github-app-${APP_SLUG}-pem" >/dev/null && echo "PEM present"
```

```powershell
# Windows (PowerShell)
Test-Path "$env:USERPROFILE\.secrets\github-app-$env:APP_SLUG-pem.xml"
```

If absent, ask the developer to manually store the PEM (the manifest
flow may have failed mid-write) and re-run from this step.

## Step 3 — Install on the target repo 🛑

Open the install URL in the developer's browser:

```sh
# macOS
open "$APP_INSTALL_URL"
# Linux
xdg-open "$APP_INSTALL_URL"
# Windows (PowerShell)
Start-Process "$env:APP_INSTALL_URL"
```

**🛑 HUMAN CHECKPOINT 2.** The developer sees "Install <app-name>",
selects **Only select repositories**, picks `<owner>/<repo>`, clicks
**Install**.

Poll for the installation. Cap at 3 minutes:

```sh
OWNER="$(echo <owner>/<repo> | cut -d/ -f1)"

for _ in $(seq 36); do
  if eval "$(python3 scripts/list-installs.py \
              --slug "$APP_SLUG" \
              --app-id "$APP_ID" \
              --account "$OWNER" 2>/dev/null)"; then
    break
  fi
  sleep 5
done
[ -n "$INSTALL_ID" ] || { echo "install not detected"; exit 1; }
echo "INSTALL_ID=$INSTALL_ID"
```

`scripts/list-installs.py` is an opaque consumer: it reads the PEM
from the secret store, mints a short-lived App JWT in memory, calls
`/app/installations`, and prints `INSTALL_ID=<n>` for `eval`. The
agent never sees PEM bytes. (Do not substitute
`gh api /repos/.../installation` here — that endpoint requires the
App's JWT, so a user PAT returns 401.)

If the developer installed on a different repo or under a different
account (e.g. an org instead of their personal account), the poll
times out. Ask them to confirm what they selected.

## Step 4 — Recommend branch protection (advisory)

**The agent does not configure branch protection.** Repo settings on
customer projects are usually owned by the customer's GitHub admin
(an org owner, security team, or release manager), not by the
individual developer the bot runs for. Even when the developer has
admin rights, applying protection programmatically can collide with
existing org policy or invalidate compliance posture set by the
customer. So this step is purely advisory: the agent prints the
recommended policy and tells the developer to apply it (or to ask
the customer admin to apply it) before running the smoke test.

Print this recommendation to the developer verbatim:

> ⚠️ **Action required.** The bot identity is most useful when the
> default branch is protected so the bot can't push directly and the
> human reviewer must approve. Before running Step 7, please apply
> these rules to `<owner>/<repo>`'s default branch — or ask the
> repo admin / customer-side admin to apply them:
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
>
> If the developer or admin chooses not to apply protection, the
> setup will still complete, but the bot can technically push
> directly to the default branch and self-review is no longer
> mechanically prevented — only the bot identity convention.

Do **not** call `gh api -X PUT .../protection` or `POST .../rulesets`.
Do **not** infer the developer's admin status. Do **not** wait for
confirmation that the rules were applied — record the recommendation
and continue.

## Step 5 — Scaffold local files in the engagement repo

Copy each file from this repo's `templates/` directory into the
developer's engagement repo. Substitute placeholders inline:

| Source (this repo)                  | Destination (engagement repo) | Substitute                                  |
|-------------------------------------|-------------------------------|---------------------------------------------|
| `templates/bin/app-token.py`        | `bin/app-token.py`            | (none — verbatim)                           |
| `templates/bin/agent-env.sh`        | `bin/agent-env.sh`            | `<APP_ID>`, `<INSTALL_ID>`, `<APP_SLUG>`    |
| `templates/bin/agent-env.ps1`       | `bin/agent-env.ps1`           | `<APP_ID>`, `<INSTALL_ID>`, `<APP_SLUG>`    |
| `templates/bin/agent`               | `bin/agent`                   | (none)                                      |
| `templates/bin/agent.ps1`           | `bin/agent.ps1`               | (none)                                      |
| `templates/git-hooks/pre-commit`    | `.git/hooks/pre-commit`       | `<APP_SLUG>`                                |

Make Unix scripts executable:

```sh
chmod +x bin/app-token.py bin/agent-env.sh bin/agent .git/hooks/pre-commit
```

Verify the substitutions took (no placeholders left):

```sh
grep -E '^(APP_ID|INSTALL_ID|APP_SLUG)=' bin/agent-env.sh
# → APP_ID="<numeric>"
# → INSTALL_ID="<numeric>"
# → APP_SLUG="<the slug GitHub returned>"

! grep -n '<APP_ID>\|<INSTALL_ID>\|<APP_SLUG>' bin/*.sh bin/*.ps1 .git/hooks/pre-commit \
  || { echo "placeholders remain"; exit 1; }
```

## Step 6 — Configure git in the engagement working tree

The credential helper below feeds the bot's installation token to
`git push`, but git only invokes credential helpers for **HTTPS**
remotes — not SSH. Make sure `origin` is an HTTPS URL, switching it
if needed:

```sh
case "$(git remote get-url origin)" in
  https://github.com/*) ;;                 # already HTTPS, leave it
  git@github.com:*)
    new="$(git remote get-url origin \
            | sed -E 's|^git@github\.com:|https://github.com/|')"
    git remote set-url origin "$new"
    ;;
  *)
    echo "unexpected remote URL; flag to developer" >&2
    exit 1 ;;
esac
```

If `origin` stays SSH, `git push` will use the developer's personal
SSH key and the push will be attributed to the developer, not the
bot — which silently defeats the whole bot identity setup.

Then set the identity:

```sh
git config user.name  "${APP_SLUG}[bot]"
git config user.email "${APP_ID}+${APP_SLUG}[bot]@users.noreply.github.com"
git config credential.helper '!f() { echo username=x-access-token; echo password=$GH_TOKEN; }; f'
```

The credential helper string is single-quoted intentionally — the
shell helper expands `$GH_TOKEN` at push time, not at config time.

## Step 7 — Smoke test 🛑

Bot side, via the launcher from Step 5:

```sh
bin/agent sh -c '
  set -e
  gh auth status
  git checkout -b agent-smoke-test
  echo smoke > smoke.txt
  git add smoke.txt
  git commit -m "smoke test"
  git push -u origin agent-smoke-test
  gh pr create --title "smoke" --body "smoke test"
'
```

Confirm the PR author is the bot. Note the gh CLI uses `app/<slug>`
as the author filter for App-authored PRs, **not** `<slug>[bot]`:

```sh
PR_NUM="$(bin/agent gh pr list \
  --author "app/${APP_SLUG}" \
  --state open --json number --jq '.[0].number')"

bin/agent gh pr view "$PR_NUM" --json author --jq .author.login
# expected: app/<APP_SLUG>
```

If `gh pr list` returns nothing on a brand-new private repo, see the
search-index note below — the PR exists, search just hasn't indexed
it. Fall back to the REST list:

```sh
PR_NUM="$(bin/agent gh api repos/<owner>/<repo>/pulls \
            --jq '.[] | select(.head.ref=="agent-smoke-test") | .number')"
```

**🛑 HUMAN CHECKPOINT 3.** Tell the developer to run, in **their**
shell (not the bot's, with `GH_TOKEN` unset):

```sh
GH_TOKEN= gh pr review <PR_NUM> --approve --repo <owner>/<repo>
GH_TOKEN= gh pr merge  <PR_NUM> --squash --delete-branch --repo <owner>/<repo>
```

```powershell
$env:GH_TOKEN = ""
gh pr review <PR_NUM> --approve --repo <owner>/<repo>
gh pr merge  <PR_NUM> --squash --delete-branch --repo <owner>/<repo>
```

Or via browser: open `https://github.com/<owner>/<repo>/pull/<PR_NUM>`
directly and click **Approve** + **Merge pull request**. The direct
PR URL works even when the listing page doesn't (see below).

Wait for the developer to confirm the merge succeeded. If they hit
"you cannot approve your own pull request", the bot identity didn't
take in Step 6 — re-check `git config user.email` in this working
tree, and re-check that `origin` is HTTPS.

> ⚠️ **Heads-up on new private repos: search indexing lag.** GitHub
> indexes a private repo's issues and PRs into its search backend
> on first activity, with a delay that's usually minutes but can
> stretch to hours. Until indexing catches up:
>
> - The repo's `/pulls` listing page can show "There aren't any
>   open pull requests" *while the tab badge counts the PR* — the
>   listing is search-driven, the badge isn't.
> - `gh pr list` and `--author` filters return empty.
> - The direct PR URL (`/<owner>/<repo>/pull/<n>`) works fine and
>   so does `gh pr view <n>` and `gh api repos/.../pulls`.
>
> Tell the developer this up front so the empty listing isn't a
> blocker for the smoke-test approval. Use the direct URL or `gh
> api .../pulls` to find the PR.

## Step 8 — Hand off

Report to the developer:

```
✅ Setup complete.

App:           <APP_SLUG>           (id <APP_ID>)
Installed on:  <owner>/<repo>       (install id <INSTALL_ID>)
PEM:           OS secret store, key github-app-<APP_SLUG>-pem
Smoke PR:      #<PR_NUM> merged at <SHA>

Day-to-day: launch your agent CLI via `bin/agent <cli>` (e.g.
`bin/agent claude`, `bin/agent codex`). The launcher mints a fresh
1-hour installation token and exports the bot identity into the
session.

Re-source `bin/agent-env.sh` (Unix) or `bin/agent-env.ps1` (Windows)
any time the token expires.
```

If anything didn't go to plan, list the failures with the matching
step number; let the developer decide retry vs. manual.

## Step 9 — Rotate the PEM (when needed)

Rotate **whenever** the PEM may have been exposed: agent or human
echoed it (in any encoding), it landed in a log / transcript / chat,
the workstation was lost or shared, or it's just been a long time.
Rotation is cheap; treat it as the default response to any doubt.

```
Rotation flow (3 minutes, one human checkpoint):

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

3. Replace the keychain entry without touching the file content
   yourself. Pipe the file straight into the OS secret store and
   delete it (overwriting on Unix, `Remove-Item` on Windows).
   Do not `cat`, `head`, `grep`, or otherwise read it.

   macOS:
   ```sh
   PEM_FILE="$HOME/Downloads/<APP_SLUG>.<date>.private-key.pem"
   security delete-generic-password -s "github-app-<APP_SLUG>-pem" 2>/dev/null
   security add-generic-password \
     -s "github-app-<APP_SLUG>-pem" -a "$USER" \
     -w "$(cat "$PEM_FILE")"
   rm -P "$PEM_FILE"
   ```

   Linux (libsecret):
   ```sh
   secret-tool store --label github-app-<APP_SLUG>-pem \
     service github-app-<APP_SLUG>-pem < "$PEM_FILE"
   shred -u "$PEM_FILE"
   ```

   Windows (PowerShell):
   ```powershell
   $Pem = Get-Content "$HOME\Downloads\<file>.pem" -Raw
   $Secure = ConvertTo-SecureString $Pem -AsPlainText -Force
   $Secure | Export-Clixml "$env:USERPROFILE\.secrets\github-app-<APP_SLUG>-pem.xml"
   Remove-Variable Pem, Secure
   Remove-Item "$HOME\Downloads\<file>.pem"
   ```

4. Verify by minting an installation token end-to-end. Discard the
   output via `>/dev/null`; never print:

   ```sh
   . bin/agent-env.sh && [ -n "$GH_TOKEN" ] && echo "rotation ok"
   ```

5. 🛑 HUMAN CHECKPOINT — tell the developer to revoke the **old**
   private key in the same App settings page ("Private keys" →
   delete the old key by date). This invalidates any token already
   minted from the old PEM, including ones that may have leaked.
```

## Fallbacks

### Debugging the token mint

If `bin/agent-env.sh` or `bin/app-token.py` fails — never inspect
the PEM. Use only these diagnostics:

- **Is the keychain entry present?**
  `security find-generic-password -s "github-app-${APP_SLUG}-pem" >/dev/null && echo present`
  (macOS; analogous `secret-tool lookup` / `Test-Path` on other OSes).
- **Is `pyjwt` installed?**
  `python3 -c "import jwt, requests; print('ok')"`
- **Does the JWT mint succeed?** Run `bin/app-token.py` end-to-end;
  the failure mode is in its `r.raise_for_status()` line, which prints
  the *GitHub error* — not the PEM. If you see `401 invalid JWT
  signature`, suspect a stored-format mismatch (decode handling in
  `app-token.py`).
- **Is the token shape right?**
  `. bin/agent-env.sh && [ -n "${GH_TOKEN%[!ghs_]*}" ] && echo ok`
  (length / prefix check, no value emitted).

If the PEM itself is suspect, **rotate** (Step 9) — do not
investigate by reading bytes.

### Other fallbacks

- **No browser available (SSH / headless box).** Print the manifest
  URL and ask the developer to open it on a workstation, then paste
  the redirect URL with `?code=…&state=…` back so you can complete
  the conversion via `POST /app-manifests/{code}/conversions`.
- **Developer's `gh` is logged in as the bot when human action is
  needed.** Have them open a fresh terminal with `GH_TOKEN` unset, or
  run `gh auth switch`.
- **`scripts/manifest-flow.py` fails with `ModuleNotFoundError`.**
  Run `pip install pyjwt cryptography requests` (per Pre-flight) and
  retry. On macOS Homebrew Python, you may need
  `--break-system-packages` (PEP 668) or a dedicated venv.
- **Anything else.** Report it. Let the developer decide retry vs.
  manual.
