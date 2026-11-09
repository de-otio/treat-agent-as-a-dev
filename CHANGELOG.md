# Changelog

## v0.7.1

- **A 422 from `taaad mint` now names both usual causes.** GitHub answers
  HTTP 422 when the repository is not in the installation's selected
  repositories, as well as when it refuses a permission level. v0.7.0's
  hint only suggested `--exclude`, which sent people the wrong way in the
  first case. Under `read` the hint now names both; under `write` and
  `contents-write`, which used to give no hint, it names the repository
  cause. Exit codes and the final `taaad: error=github status=422` line
  are unchanged.

## v0.7.0

- **`taaad mint --permissions read` follows the installation.** It now
  requests every permission the installation grants, each at `read`,
  instead of the fixed `metadata`, `contents`, `pull_requests`, `issues`.
  A permission added to the App (code scanning, Actions, checks, …) works
  as soon as a person accepts it on the installation, with no taaad
  release. Permissions with no read level (`workflows`, `profile`) are left out.
- **Tokens can be wider than in v0.6.0, but only at `read`.** On an
  installation that grants more than the four keys, a `read` token now
  carries those too, org-level permissions included. See SECURITY.md for
  the sensitive ones.
- **New `--only PERM,...` and `--exclude PERM,...`** (read class only,
  mutually exclusive). `--only` keeps the v0.6.0 shape:
  `--only metadata,contents,pull_requests,issues`.
- An HTTP 422 from GitHub under `read` now suggests `--exclude`. Exit
  codes are unchanged.
- **`taaad mint` ends every failure with one fixed stderr line,**
  `taaad: error=<code>[ status=<http>]`, so a caller such as repo-aegis
  can name the failed step without showing taaad's free text. The
  helper's error codes pass through verbatim; the list is in the README.
- **Needs repo-aegis ≥ 0.13.0.** Older repo-aegis refuses these tokens as
  wider than the requested scope.

## v0.6.0

Adds `taaad-signer`, a small signed and notarised macOS helper that holds the
App private keys, so that no script running as you can read them (see v0.5.6
for why the old keychain item could not guarantee that).

**There is no v0.5.6 release.** It was never tagged on its own; everything
listed under "v0.5.6" below (the corrected macOS security claims, the new
`doctor` check, `taaad mint`) ships in this version.

- **`taaad-signer` (macOS, optional).** The helper keeps each App's PEM in
  the data-protection keychain under its own access group. Other programs,
  including the Python interpreter and `security`, cannot see the item at
  all. The helper signs the GitHub App JWT itself; no mint path returns the
  key. Install it from the notarised `.pkg` on the GitHub release (RUNBOOK
  Step 0, `signer/RELEASING.md`).
- **Touch ID or password before each fresh token.** Each mint that is not
  served from the helper's token cache asks for presence, with the App and
  the narrowed permissions in the prompt. A token cached by the helper is
  reused until five minutes before it expires, with no new prompt. Prompts
  are rate limited.
- **Routing.** `taaad register`, `rotate`, `mint`, `doctor`, `apps` and
  `uninstall` use the helper when it is installed and never fall back to the
  old keyring after a helper error. Without the helper, behaviour is as in
  v0.5.6.
- **`taaad mint` exit codes:** 0 ok, 2 refused (stdout is a terminal), 3 rate
  limited, 4 presence denied, 1 anything else (including key not found and
  GitHub errors). The helper's own exit codes are a separate table (SPEC
  section 4). *Corrected after release: the v0.6.0 tag lists 3 as "key not
  found", which is wrong.*
- **Migration:** rotate each App through the helper (RUNBOOK Step 9). A key
  that was in the old keychain item stays readable by whatever the access
  list in `taaad doctor` shows until you rotate it.
- **Limits, stated in SECURITY.md:** the Developer ID signing identity is the
  root of trust; a Touch ID prompt is not proof of who asked; one approval
  covers the cached token; the PEM passes through Python once when you
  register or rotate.
- Build and release: `signer/scripts/{build,install,package}.sh`; the Swift
  core has 69 unit tests, the Python side 244 in total. The test suite can no
  longer reach a helper installed on the developer's machine.

## v0.5.6

Corrects a false security claim about the macOS Keychain and makes
`taaad doctor` report the real state. Also ships `taaad mint`.

- **New `taaad mint --app <slug> --repo <owner>/<repo> --permissions
  <read|write|contents-write> --json`.** Mints an installation token
  narrowed to one repository and one permission class (intersected
  with the installation's granted permissions) and prints a single
  JSON object. Refuses a TTY (no `--force`), a repo owner that is not
  the installation account, and any response that lists more than the
  one requested repository. The token never appears in errors; HTTP
  error bodies are redacted.
- New `github.get_installation`, `github.create_scoped_token`,
  `github.redact`.
- **The macOS keychain item trusts the Python interpreter, not
  `taaad`.** python-keyring stores the PEM from the interpreter, and
  the Keychain trusts whichever executable created an item. Any
  script that interpreter runs can read the PEM without a prompt.
  Pre-v0.5.6 docs said `taaad register` scoped the item to the
  `taaad` binary; it did not (see "Removed" below). SECURITY.md now
  has a "macOS keychain access list" section stating this plainly.
- **New `taaad doctor` check (macOS).** For every registered App,
  `doctor` reads the item's access list with `security
  dump-keychain -a` (never `-d`: no secret is decrypted or printed)
  and fails unless the only application allowed to read the PEM is
  the signed taaad helper. Fails closed if the list cannot be read.
  The parsing and verdict live in the new pure module `taaad.acl`.
- **Heads-up: `doctor` now exits non-zero on every macOS install**
  until the signed helper ships, because no real item can pass.
  Anything that gates on `doctor`'s exit status will go red.
- **Removed `secrets.restrict_macos_acl`.** It ran `security
  add-generic-password -U -s … -T <taaad>` after storing the key.
  `-T` adds a trusted application and never removes one; the launcher
  is a Python script, so the Keychain never sees it as the reader;
  and the step raised two login-password Keychain dialogs with no
  indication that taaad caused them (the first offering *Always
  Allow*), while errors were swallowed. `taaad register` now prints a
  note about the limitation instead.
- Docs: SECURITY.md, RUNBOOK Step 1 and `docs/architecture.md` no
  longer claim a per-binary ACL.
- **Existing keys:** treat any PEM stored before the helper exists as
  readable by any program `doctor` lists. Rotate (RUNBOOK Step 9)
  once the helper is available. If `doctor` lists
  `/usr/bin/security`, an earlier *Always Allow* made the key
  readable by any shell command; rotate that App sooner. Note that
  `taaad rotate` updates the item in place and keeps its access list,
  so delete the old Keychain item first for a clean list.
- Dev: new `dev` extra (`pytest`, `hypothesis`). Tests: +26 (63 total),
  including property tests that no item with a non-helper application
  can pass, against redacted `security dump-keychain` fixtures.

## v0.5.5

`taaad doctor` continuously enforces Operating Rule 8 (Apps must
stay private). Closes the last finding from the v0.5.3 security
review.

- **`taaad doctor` queries `GET /app` on every run** and emits
  `[FAIL] App is PUBLIC — Operating Rule 8 says it must stay
  private` if the public flag has drifted. Pre-v0.5.5, Rule 8 was
  only enforced at App creation time (`manifest.public = false`)
  — anyone with App-edit rights on github.com could flip "Where
  can this GitHub App be installed?" to *Any account* afterwards
  and `taaad doctor` would happily report the App as healthy. The
  new check makes the rule continuous, not one-shot.
- **New API client function `github.get_app_meta(app_id, pem)`**
  wraps `GET /app`. Authenticates as the App via JWT (not as a
  user), so it works for both user-owned and org-owned private
  Apps — unlike `GET /apps/<slug>` which was removed in v0.5.2 for
  exactly that reason.
- The check runs **before** the install-token mint and is
  independent of `install_id` — an App without an install can
  still be public and that's still a violation.
- Tests: +3 in a new `test_github.py`. Total now 37, all passing.

## v0.5.4

Security hardening release. Closes findings from a v0.5.3 security
review of the `taaad register` localhost server, the SSH host alias
resolver added in v0.5.3, and the `taaad env` token-printing path.
No API breakage; one behavioural change (`taaad env` now refuses a
TTY by default — see below).

- **In-process SSH config parser replaces `ssh -G`.** v0.5.3 shelled
  out to `ssh -G <host>` to resolve aliases like
  `git@github.com-personal:…`. `ssh -G` fully evaluates the SSH
  config including `Match exec` directives, which run arbitrary
  shell commands during resolution — making `taaad init` a novel
  trigger for any latent payload in a developer's `~/.ssh/config`.
  v0.5.4 walks `~/.ssh/config` and `/etc/ssh/ssh_config` directly
  with a strict hostname-pattern parser (literal/glob `Host`
  blocks, `!`-negation, `Include` directives, cycle detection).
  `Match` blocks are skipped entirely — the parser never runs
  anything from the config file. If your alias lives in a `Match
  host` block, move it to a plain `Host` block for taaad to find
  it (`Match host` was never strictly needed for that use case).
- **`taaad env` refuses to print to a TTY by default.** The
  command emits `export GH_TOKEN=ghs_…` for shell `eval`. The old
  behaviour printed unconditionally; running `taaad env` standalone
  (without `eval $(...)`) landed the installation token in
  scrollback, terminal history, and any logging tool watching the
  TTY. v0.5.4 detects `sys.stdout.isatty()` and refuses with a
  helpful error pointing at `eval "$(taaad env)"`. `--force`
  bypasses the guard for unusual debugging. A stderr warning
  (`# do not log this output`) is also emitted on every successful
  invocation.
- **`taaad register` now path-validates the binary before macOS
  ACL.** The keychain ACL restriction (`security
  add-generic-password -T <abs-taaad>`) was being called with
  `paths.taaad_executable()` directly. Other call sites
  (`init.py`, `credential_helper.py`) wrap that with
  `paths.assert_path_safe()` to verify ownership and mode of every
  path component before trusting it. `register.py` now does the
  same.
- **PEM lifetime in `register.py` reduced.** The PEM is now
  `pop`-ed off the response dict (rather than read), the local
  binding is `del`-ed immediately after the keyring write, and
  the response dict itself is `del`-ed. This does not zero the
  bytes (CPython strings are immutable; `requests.Response` and
  `json.loads` interim strings still hold copies until garbage
  collection), but it tightens the explicit-reference window.
  SECURITY.md now documents this honestly under "Residual risks".
- **SECURITY.md: new "Residual risks we accept" section.** Covers
  the in-memory PEM hygiene gap above, the ineffectiveness of
  `taaad rotate`'s file shred on copy-on-write filesystems (APFS,
  btrfs, ZFS), and the `taaad agent` allowlist's role as a
  guardrail rather than a sandbox.
- **Tests: 34 total, +11 new.** SSH config parser fixtures cover
  literal match, glob and negation patterns, first-match-wins
  semantics, `Match` block skipping (security-critical), `Match`-only
  configs returning the input unchanged, `Include` recursion,
  `Include` cycles, quoted values, and missing-config graceful
  degrade. New `test_env.py` covers TTY refuse, `--force` bypass,
  and pipe + stderr warning.

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
