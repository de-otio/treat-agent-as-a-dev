# Changelog

## v0.5.3

Bug fixes and breadth improvements to `taaad register` and the URL
rewrite in `taaad init`. Two new operating rules in the runbook
(Apps must stay private; slug visibility = public PR-author handle).

- **Bug fix: `taaad register` callback rejected by its own server.**
  The localhost server's CSRF guard rejected `Sec-Fetch-Site:
  cross-site` on every GET, including the `/callback` endpoint —
  but `/callback` is *necessarily* cross-site (it's the redirect
  from github.com). Modern browsers always send the header on
  redirect navigations, so the manifest flow failed end-to-end with
  `403 cross-site request rejected` on the legitimate path. The
  guard now applies only to `/` (where the CSRF concern is real,
  since that page auto-POSTs to GitHub on load). `/callback` is
  guarded by the `state` token instead.
- **New: `--org <org>` flag for `taaad register`.** Routes the
  manifest flow to
  `https://github.com/organizations/<org>/settings/apps/new` so the
  App is owned by the organization, not the registering user.
  Required when the target repo lives in an org and the App is
  private (Operating Rule 8): private user-owned Apps cannot be
  installed on a separate org. Without `--org`, the
  install in Step 3 fails with no installation found, and the only
  fixes are either a public App (forbidden by Rule 8) or
  re-registration with the right ownership.
- **Robust SSH URL rewrite in `taaad init`.** `coerce_https` and
  `parse_owner` now recognise:
  - canonical `git@github.com:…` (unchanged)
  - `ssh://git@github.com[:port]/…` form
  - SSH host aliases (`git@github.com-personal:…`,
    `git@gh-work:…`) — resolved by shelling out to `ssh -G <host>`
    and reading the `hostname` line, the same path the real ssh
    client uses, so any alias git can connect through is
    rewritable.
  Without this, multi-account developers ended up with origin still
  pointing at SSH after `taaad init`, the credential helper never
  ran, and pushes were attributed to the developer's SSH key
  instead of the bot's installation token. `_resolve_ssh_hostname`
  is `lru_cache`'d and degrades gracefully (5 s timeout, falls back
  to literal-host matching when ssh is missing or fails).
- **Operating Rule 8: Apps must stay private.** `manifest.public`
  is `false` by default; do not toggle "Where can this GitHub App
  be installed?" to *Any account* on github.com after creation.
  When a private user-owned App can't be installed on the target
  org, the answer is `--org`, not making it public.
- **Operating Rule 9: Slug visibility.** `<APP_SLUG>[bot]` is the
  public PR-author handle on every commit and PR the bot opens.
  The slug must not contain customer / client / employer names if
  the target repo is or could become public.
- **Runbook: Inputs / Step 1 / Step 3 / Step 5 updated** to cover
  the user-vs-org ownership decision, the `--org` flag, the
  install-target mismatch troubleshooting path, and the residual
  cases where SSH-to-HTTPS rewrite still needs a manual override
  (`insteadOf` rules, `ssh -G` unavailable).
- New tests in `test_git.py` for `ssh://` form, SSH aliases
  (monkeypatched resolver), `parse_owner` variants, and graceful
  degrade when ssh is missing. 23/23 passing.
- No security implications. The Sec-Fetch-Site fix preserves
  the `state` CSRF guard on `/callback` and tightens the cross-site
  check to where it actually applies. The new `ssh -G` resolution
  is a local subprocess call with a 5 s timeout; failure modes
  fall back to the previous behaviour, never to a broader rewrite.

## v0.5.2

Bug fix for `taaad install` against private Apps.

- `GET /apps/<slug>` requires a user access token from the App's
  owner; minting an App JWT against it returns 401 (private App)
  or 404 (unauth). v0.5 used this for self-discovery of `app_id`
  during re-enrolment, which broke for private Apps.
- `taaad install` now trusts the `--app-id` flag and skips the
  `/apps/<slug>` call. The next API call
  (`/app/installations` with the JWT minted from `app_id`) is the
  real test — if the (PEM, app_id) pair is wrong, that call
  rejects.
- `github.get_app_by_slug` removed (it never worked for private
  Apps).
- No security implications — fails closed.

## v0.5.1

Bug fix for re-enrolment from v0.4 keychain entries on macOS.

- `secrets.get_pem` now transparently hex-decodes the value
  returned by macOS `security ... -w` when the stored content
  contains newlines (any PEM does). Without this, JWT minting
  fails with 401 against `/apps/<slug>` because the "PEM" passed
  to `jwt.encode` is actually its hex encoding. v0.4's
  `app-token.py` had this decode inline; it was dropped in v0.5
  by mistake. New v0.5.0 registrations on macOS are also affected
  on subsequent reads.
- New tests: hex-encoded round-trip via `keyring`, plain
  passthrough, presence check.
- No security implications — the bug fails closed (operations
  fail rather than silently mishandling the PEM).

## v0.5.0

Hard cut from v0.4. Replaces the per-repo shell + PowerShell
scaffolding with a single `taaad` CLI installed once per machine
via `pipx`.

### Breaking

- `templates/` and `scripts/manifest-flow.py` /
  `scripts/list-installs.py` are removed. The flow is now driven by
  `taaad` subcommands (see [RUNBOOK.md](RUNBOOK.md)).
- Per-repo `bin/agent`, `bin/agent-env.sh`, `bin/agent-env.ps1`,
  `bin/app-token.py`, and `.git/hooks/pre-commit` are no longer
  scaffolded. The repo carries no `bin/`; per-repo state is three
  keys in `.git/config` (`bot.app`, `user.name`/`user.email`,
  `credential.helper`).
- v0.4 keychain entries are reused as-is — no PEM migration.
- See [README.md → Re-enrolling existing engagements](README.md#re-enrolling-existing-engagements)
  for the per-repo flow.

### New CLI

`taaad register | install | apps {list,show,remove} | init | uninit |
agent | env | credential-helper | rotate | doctor | hooks
{install,uninstall} | uninstall`.

Distribution:
`pipx install git+https://github.com/de-otio/treat-agent-as-a-dev@v0.5`.

### Security fixes vs v0.4

- Browser callback now validates `Host`, `Origin`, and
  `Sec-Fetch-Site` headers (design §6).
- Credential helper re-verifies its binary path's ownership and
  mode at every invocation, not just at init (design §12).
- `taaad agent` enforces a configurable allowlist of child
  binaries (default: `claude codex aider gh git`) and refuses
  argv containing `$GH_TOKEN` or the literal token value
  (design §13).
- `taaad doctor` warns on `GIT_TRACE*` / `GIT_CURL_VERBOSE` env
  vars (design §14).
- Linux keyring plaintext-fallback backends are refused at startup
  (design §7).
- `taaad rotate` writes-then-verifies-then-shreds, never
  delete-before-verify (design §18).
- `taaad doctor` checks `core.hooksPath` precedence via `git
  config --show-origin` (design §19).
- `taaad uninit` value-matches before unsetting any `.git/config`
  key (design §20).

### Other

- Cross-platform via `keyring` (replaces shell + PowerShell mirror)
  and `platformdirs` (replaces hard-coded paths).
- Pre-commit hook moved to `<config-dir>/hooks/pre-commit`,
  installed via `taaad hooks install` (sets `core.hooksPath`,
  refuses to clobber existing values).
- `apps/<slug>.toml` schema versioning: `taaad` refuses files with
  newer `schema_version` than it knows; older are auto-migrated.
- Pointer: README → "Re-enrolling existing engagements".

## v0.4.0

(See git history for v0.4 and earlier.)
