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

1. **The PEM never touches disk in plaintext.**
   `scripts/manifest-flow.py` captures the PEM in memory from the
   conversion API response and writes it directly to the OS secret
   store. Do not log, echo, paste, or redirect it to a file.
2. **No secret in commits.** App ID, Installation ID, slug, and the
   bot's `users.noreply.github.com` email are not secrets and may be
   committed. The PEM, the webhook secret, and any installation token
   are secrets and must not be.
3. **`GH_TOKEN` lives in environment only.** Never write it to disk.
4. **Stop on failure.** Surface the exact error and which step
   failed. Let the developer choose retry vs. manual fallback. Do
   not silently retry or improvise around blocked operations.
5. **Pause at human checkpoints.** Three steps require a click in the
   developer's browser. Each is marked `🛑 HUMAN CHECKPOINT`. Do not
   proceed past one until the developer has confirmed.
6. **Ask, don't guess.** If a required input is missing, ask. Do not
   invent values for `<engagement>`, `<dev>`, `<owner>/<repo>`,
   `<gh-user>`, or `<bot-name>`.

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
| `<dev>`           | `alice`            | developer slug; lowercase, no spaces           |
| `<owner>/<repo>`  | `acme-co/widgets`  | target repo on github.com                      |
| `<gh-user>`       | `alice-jones`      | the developer's GitHub handle                  |
| `<bot-name>`      | `acme-alice-agent` | optional; overrides default `<engagement>-<dev>-bot` slug |

The default bot name is `<engagement>-<dev>-bot`. If the developer
wants a different name, capture it as `<bot-name>` and pass it via
`--name` in Step 1. Either way, GitHub returns the canonical slug
after creation as `APP_SLUG`; everything downstream uses that.

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
what will be used for branch protection (Step 4) and the smoke-test
approval (Step 7). It must remain logged in throughout.

## Step 1 — Register the App via manifest flow 🛑

Run [`scripts/manifest-flow.py`](scripts/manifest-flow.py). With the
default name (`<engagement>-<dev>-bot`):

```sh
eval "$(python3 scripts/manifest-flow.py \
  --engagement <engagement> \
  --dev <dev> \
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
for _ in $(seq 36); do
  if INSTALL_ID="$(gh api /repos/<owner>/<repo>/installation --jq .id 2>/dev/null)"; then
    break
  fi
  sleep 5
done
[ -n "$INSTALL_ID" ] || { echo "install not detected"; exit 1; }
echo "INSTALL_ID=$INSTALL_ID"
```

If the developer installed on a different repo or scope, the poll
times out. Ask them to confirm what they selected.

## Step 4 — Configure branch protection

Resolve the default branch — don't assume `main`:

```sh
DEFAULT_BRANCH="$(gh api repos/<owner>/<repo> --jq .default_branch)"
```

Apply the policy via API:

```sh
cat > /tmp/branch-protection.json <<'EOF'
{
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_last_push_approval": true
  },
  "enforce_admins": null,
  "restrictions": null,
  "required_status_checks": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF

gh api -X PUT \
  "repos/<owner>/<repo>/branches/$DEFAULT_BRANCH/protection" \
  --input /tmp/branch-protection.json

rm /tmp/branch-protection.json
```

If `gh api` returns 403, the developer isn't a repo admin. Ask the
customer-side admin to apply the rules manually before continuing —
do not skip this step.

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

Confirm the PR author is the bot:

```sh
PR_NUM="$(bin/agent gh pr list \
  --author "${APP_SLUG}[bot]" \
  --state open --json number --jq '.[0].number')"

bin/agent gh pr view "$PR_NUM" --json author --jq .author.login
# expected: <engagement>-<dev>-bot[bot]
```

**🛑 HUMAN CHECKPOINT 3.** Tell the developer to run, in **their**
shell (not the bot's, with `GH_TOKEN` unset):

```sh
GH_TOKEN= gh pr review <PR_NUM> --approve
GH_TOKEN= gh pr merge  <PR_NUM> --squash --delete-branch
```

```powershell
$env:GH_TOKEN = ""
gh pr review <PR_NUM> --approve
gh pr merge  <PR_NUM> --squash --delete-branch
```

Wait for the developer to confirm the merge succeeded. If they hit
"you cannot approve your own pull request", the bot identity didn't
take in Step 6 — re-check `git config user.email` in this working
tree.

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

## Fallbacks

- **No browser available (SSH / headless box).** Print the manifest
  URL and ask the developer to open it on a workstation, then paste
  the redirect URL with `?code=…&state=…` back so you can complete
  the conversion via `POST /app-manifests/{code}/conversions`.
- **Developer's `gh` is logged in as the bot when human action is
  needed.** Have them open a fresh terminal with `GH_TOKEN` unset, or
  run `gh auth switch`.
- **`gh api ... /protection` returns 403.** Developer is not a repo
  admin. Either elevate or have the customer-side admin apply branch
  protection manually before continuing.
- **`scripts/manifest-flow.py` fails with `ModuleNotFoundError`.**
  Run `pip install pyjwt cryptography requests` (per Pre-flight) and
  retry.
- **Anything else.** Report it. Let the developer decide retry vs.
  manual.
