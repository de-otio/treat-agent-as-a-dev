# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
<https://github.com/de-otio/treat-agent-as-a-dev/security/advisories/new>

This CLI touches the user's keychain, creates GitHub Apps under
the user's account, and mints installation tokens. If you find a
flaw — particularly around the manifest flow, the local callback
server, the credential helper, or how the PEM is captured and
stored — please report it privately rather than opening a public
issue. (Public issues are disabled on this repo precisely so
reports go through the private channel.)

## Supported versions

Only the most recent tagged release is supported.

## Trust boundaries

- The PEM transits Python → `keyring` → the OS-native secret store
  (Keychain on macOS, Secret Service / KWallet on Linux, Credential
  Manager on Windows).
- Installation tokens are minted in `taaad`'s own process memory
  and emitted either as `GH_TOKEN` to a child process (`taaad
  agent`) or to git's stdin via the credential helper. They are
  never written to disk, never cached.
- The agent reads `RUNBOOK.md` and invokes `taaad` subcommands. It
  must not bypass `taaad` to read or write the PEM directly (e.g.
  via `keyring`, `security`, `secret-tool`).

## Known ambient-authority limitations

On Linux Secret Service and Windows Credential Manager, **any
process running as the same user can read any secret the user has
stored** — there is no per-binary ACL. macOS Keychain *does* offer
per-item ACLs; `taaad register` invokes
`security add-generic-password -T <abs-taaad>` to scope the entry
to the `taaad` binary, so other binaries trigger an interactive
prompt before reading.

The threat model assumes the user's account is trusted. If that
assumption fails, the PEM is recoverable on Linux and Windows by
any process the attacker controls under the same user.

## Token-leakage classes to watch

- `GIT_TRACE`, `GIT_TRACE_CURL`, `GIT_CURL_VERBOSE` env vars cause
  git to log the credential helper's output (including the
  installation token) to stderr or a file. `taaad doctor` warns
  if any are set; `taaad credential-helper` strips them from its
  own subprocess scope. **Treat any leaked installation token the
  same as a leaked PEM:** rotate the App's private key (RUNBOOK
  Step 9) and uninstall + reinstall the App on the affected repos
  to invalidate any tokens already minted.
- Echoing `GH_TOKEN` (the env var) into chat, logs, or web tools.
  The token shape `ghs_…` is a marker; any leak path means rotate.
- Pasting any encoded form of the PEM (hex, base64) anywhere.
  Encodings of secrets are still secrets.

## Hardening notes

- **Pin to a tag or SHA**, not `main` (see
  [README.md → Pinning](README.md#pinning)). The repo enforces
  immutable `v*` tags via repository ruleset and signed-tag
  requirement; SHA pinning is belt-and-braces.
- **Run `taaad doctor` before believing your bot identity is
  active.** It mints a token end-to-end as a no-secret-leakage
  shape check.
- **Never substitute the keyring backend silently.** If
  `gnome-keyring-daemon` / `kwallet` aren't reachable, `keyring`
  may fall back to a plaintext backend. `taaad` refuses to operate
  against backends whose name contains "Plaintext", "Null", or
  "Fail". Configure `pass` or
  [`keyrings.alt.PasswordStore`](https://pypi.org/project/keyrings.alt/)
  explicitly if your environment can't run a Secret Service.

## What `taaad` does not do

- Configure branch protection (RUNBOOK Step 4 is advisory only).
- Cache tokens to disk.
- Phone home or emit telemetry.
- Modify `.gitconfig` outside the repo `taaad init` runs in.
- Read your PEM bytes (the agent process never holds them; only
  `taaad`'s own subprocess does, transiently).
