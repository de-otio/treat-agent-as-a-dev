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
stored** — there is no per-binary ACL.

### macOS keychain access list

**Without the helper (all releases before v0.6.0, and v0.6.0 until
`taaad-signer` is installed and holds the key):** the Keychain decides
who may read an item by the *executable* that asks, and python-keyring
stores the PEM from the Python interpreter. The item therefore trusts
that interpreter (for a pipx install, a Homebrew `python3`), and
**every script it runs reads the PEM without a prompt**. An agent
session running as the user can run that interpreter, read the App's
private key, mint installation tokens for every repository the App is
installed on, and copy the key off the machine.

Releases before v0.5.6 ran `security add-generic-password -T <taaad>`
after storing the key and described that as scoping the entry to taaad.
It did not: `-T` adds an application to the list rather than replacing
it, the interpreter stayed on the list, and the `taaad` launcher is a
Python script, so the Keychain never sees it as the reader. An item also
accumulates trust over time: *Always Allow* on a Keychain prompt (for
example for `/usr/bin/security`) adds that program for good.

**With the helper (v0.6.0+, macOS):** `taaad-signer` is a small signed
app-like bundle installed root-owned in
`/Library/Application Support/taaad/`. It is the only holder of each
App key. The key lives in the **data-protection keychain**, in an
access group that only code signed with the taaad Developer ID **and**
carrying its provisioning profile can use. Other programs (Python,
`security`, any binary an agent builds) cannot see, read, delete or
plant those items, and no "Allow" dialog exists for them. The helper
has a fixed command set and no command that returns a key. Minting a
token needs Touch ID or the login password, with a prompt that names
the App, the account, the permissions and the repositories. Tokens are
cached until 5 minutes before they expire, and at most 3 prompts per
10 minutes per App are shown. `taaad register` and `taaad rotate`
store new keys there; `taaad doctor` fails while a legacy
Python-readable copy still exists.

Limits that remain with the helper:

- **The signing identity is the root of trust.** Anything signed with
  the taaad Developer ID together with its provisioning profile gets
  every App key. That identity must not be usable by an agent: keep it
  in a separate, password-locked keychain (see signer/RELEASING.md),
  and build and sign only when no agent session is running.
- **The prompt names an app, not a proof.** Any program can show a
  dialog titled "taaad-signer". Approving a look-alike gives it
  nothing, because it can't reach the access group, but the prompt
  text is only meaningful when taaad caused it.
- **One approval covers the cache.** taaad's own credential helper
  does not narrow yet (narrowing policy is repo-aegis Q5), so an
  approved token covers everything the installation grants, and any
  program running as the user can get the cached token from the helper
  until it is 5 minutes from expiry (about 55 minutes). A request with
  a *different* narrowing always prompts again.
- **Registration and rotation pass the PEM through Python once**, on
  its way from the downloaded file into the helper.
- **The first request to GitHub is sent before any prompt.** To look up
  an installation, and for `installation`, `app-meta` and
  `installations`, the helper sends a short-lived App JWT (valid 120
  seconds) before asking for presence. The helper's connection uses the
  system proxy settings and trust store. A person with administrator
  rights on the machine who installs a proxy and a trusted root
  certificate could capture that JWT and mint installation tokens with
  no prompt. The private key is not exposed by this. We accept it for
  now: it needs administrator rights, and pinning the connection to
  Apple's system roots would break the helper behind corporate
  TLS-inspection proxies. Those three read commands can also be run by
  any local process without a prompt, and reveal installation accounts
  and permissions.

The threat model assumes the user's account is trusted. If that
assumption fails, the PEM is recoverable by any process the
attacker controls under the same user.

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

## Residual risks we accept

These are known-and-bounded weaknesses of the current design. They
are documented here so users can make informed decisions.

- **PEM bytes in CPython process memory.** During `taaad register`
  and every `taaad credential-helper` invocation, the PEM is held
  briefly as a Python `str` (and inside `requests.Response`,
  `json.loads` interim strings, and pyjwt's encoder). CPython's
  immutable strings prevent reliable in-process zeroing; a
  local-root attacker with debugger or core-dump access can
  recover the PEM from a running process. We minimize the window
  by `del`-ing references promptly and keeping these processes
  short-lived. The keychain remains the primary persistence layer
  to attack — at which point local-root is already required.

- **`taaad rotate` shred is ineffective on copy-on-write
  filesystems.** On APFS (macOS default since High Sierra), btrfs,
  and ZFS, overwriting a file does not erase the original on-disk
  extents — they linger until the filesystem garbage-collects.
  The shred is theatre on those filesystems. Forensic recovery of
  a freshly-rotated PEM file remains possible until the FS reuses
  those blocks. Rotation itself (revoking the old PEM on
  github.com — RUNBOOK Step 9 ¶4) is the security-relevant step,
  not the file shred. Treat the on-disk PEM as compromised once
  it lands in `~/Downloads/`, and assume rotation does not
  retroactively erase it.

- **`taaad agent` allowlist is a guardrail, not a sandbox.**
  `allowed_commands` checks `os.path.basename(cmd[0])` against a
  list. A user with PATH or symlink control can trivially bypass
  it — but anyone with that level of access can also read
  `GH_TOKEN` directly from any child process's environment
  (`/proc/<pid>/environ` on Linux, `ps eauxww` on macOS). The
  allowlist exists to prevent typos and accidental misuse, not
  adversarial bypass.

- **SSH config Match exec is *not* triggered by taaad** as of
  v0.5.4. Earlier versions (v0.5.3) shelled out to `ssh -G <host>`
  to resolve aliases, which fully evaluated `Match exec`
  directives in `~/.ssh/config`. v0.5.4 replaced that with an
  in-process parser (`git._ssh_config_lookup`) that walks `Host`
  blocks only and silently skips `Match` blocks. If you depend on
  `Match host` for SSH alias resolution in your config, switch to
  plain `Host` blocks for taaad's rewrite to find them.

## What `taaad` does not do

- Configure branch protection (RUNBOOK Step 4 is advisory only).
- Cache tokens to disk.
- Phone home or emit telemetry.
- Modify `.gitconfig` outside the repo `taaad init` runs in.
- Read your PEM bytes (the agent process never holds them; only
  `taaad`'s own subprocess does, transiently).
