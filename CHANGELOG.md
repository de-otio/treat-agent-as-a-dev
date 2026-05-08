# Changelog

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
