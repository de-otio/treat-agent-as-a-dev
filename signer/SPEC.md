# taaad-signer: specification

`taaad-signer` is a small signed macOS helper and the **only** holder of each GitHub App private key. It never returns
a key. It mints installation tokens behind a user-presence prompt. taaad's Python code calls it as a subprocess on
macOS; Linux and Windows keep the `keyring` path.

Why: python-keyring items trust the Python interpreter, so any script can read the key (see SECURITY.md, "macOS
keychain access list"). Design record: plan 0003, decisions D1–D5.

## 1. Identity and packaging

| | |
|---|---|
| Bundle | `taaad-signer.app/Contents/{Info.plist, MacOS/taaad-signer, embedded.provisionprofile, _CodeSignature/}` |
| Bundle ID / code identifier | `org.de-otio.taaad-signer` (fixed for good) |
| Team | `U26DY5A9DU` (Developer ID Application) |
| Entitlements | `com.apple.application-identifier = U26DY5A9DU.org.de-otio.taaad-signer`, `com.apple.developer.team-identifier = U26DY5A9DU`, `keychain-access-groups = [U26DY5A9DU.org.de-otio.taaad-signer]`. Nothing else, and in particular no `get-task-allow`. |
| Signing | `codesign --options runtime --timestamp --entitlements …` (hardened runtime: no debugger attach, no `DYLD_*` injection) |
| Profile | Developer ID provisioning profile for that App ID. It is **not committed** (it names the maintainer); the build reads it from `$TAAAD_SIGNER_PROFILE`. |
| Installed at | `/Library/Application Support/taaad/taaad-signer.app`, owned by `root:wheel`, mode 0755 |
| Executable | `/Library/Application Support/taaad/taaad-signer.app/Contents/MacOS/taaad-signer` (called directly, never through `open`) |
| Platforms | macOS 13+, universal (arm64 + x86_64) where the toolchain allows it, otherwise arm64 |

Designated requirement (also `taaad.acl.SIGNER_REQUIREMENT`):
`identifier "org.de-otio.taaad-signer" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = U26DY5A9DU`

## 2. Inputs it trusts and does not trust

- **Trusted:** its own keychain items, argv (after validation), stdin (only for `import`), and responses from
  `https://api.github.com`.
- **Not trusted and not read:** environment variables (including `HTTPS_PROXY`, `TAAAD_*`, `XDG_*`, `HOME`), any file
  on disk, the current directory, and `apps/<slug>.toml`. The API base URL is a compile-time constant. `app_id` comes
  from the helper's own meta item, never from argv (except on `import`, where GitHub then verifies it).
- `install_id` is passed through. GitHub refuses an installation that belongs to another App, and the helper looks the
  installation up (step 4 of `mint`) before prompting.

## 3. Keychain items

All items are in the **data-protection keychain**: `kSecUseDataProtectionKeychain = true`,
`kSecAttrAccessGroup = U26DY5A9DU.org.de-otio.taaad-signer`,
`kSecAttrAccessible = kSecAttrAccessibleWhenUnlockedThisDeviceOnly`, `kSecAttrSynchronizable = false`. Other programs
can't see, read, delete or plant them (spike 2a′).

| Item | Class | Locator | Content |
|---|---|---|---|
| App key | `kSecClassKey`, RSA private | `kSecAttrApplicationTag = "taaad-signer.key.<slug>"` (UTF-8), `kSecAttrLabel = "taaad-signer <slug>"` | The key. `kSecAttrIsExtractable = false` is set but **not enforced** in this keychain, so the helper has no code path that exports or prints a key. |
| Meta | generic password | service `taaad-signer.meta`, account `<slug>` | `{"v":1,"slug":…,"app_id":N,"app_slug":…,"imported_at":"<ISO8601>"}` |
| Token cache | generic password | service `taaad-signer.token`, account `<slug>/<install_id>/<narrowing_hash>` | `{"v":1,"token":…,"expires_at":…,"permissions":{…},"repository_selection":…,"repositories":[…]}` |
| Prompt log | generic password | service `taaad-signer.rate`, account `<slug>` | `{"v":1,"prompts":[<unix seconds>,…]}` |

Lookups that can match more than one item (for example two keys with one tag) are errors (`keychain`, exit 8), never
"use the first".

## 4. Command line

`taaad-signer <command> [args]`. On success: exit 0 and **one JSON object on one line** on stdout. On failure: a
non-zero exit and one JSON object on stderr, `{"error":"<code>","message":"…"}` plus any listed extra fields. Nothing
else is printed. A token appears only in `mint`'s stdout. No key material appears anywhere, ever.

Validation: slug `^[a-z0-9][a-z0-9-]{0,38}$`; `install_id` and `app_id` decimal integers in 1…2^53; repository name
`^[A-Za-z0-9._-]{1,100}$` (at most 100, deduplicated case-insensitively); permission `NAME=LEVEL` with NAME
`^[a-z][a-z_]{0,63}$` and LEVEL one of `read`, `write`, `admin`, where a repeated NAME is a usage error.

| Command | Presence | Output |
|---|---|---|
| `version` | no | `{"version":"0.1.0","identifier":"org.de-otio.taaad-signer"}` |
| `status <slug>` | no | `{"slug":…,"present":bool,"app_id":N\|null}` |
| `import <slug> <app_id> [--replace]` (PEM on stdin) | **yes** | `{"slug":…,"app_id":N,"app_slug":…,"replaced":bool}` |
| `mint <slug> <install_id> [--repo NAME]… [--perm NAME=LEVEL]…` | **yes**, unless cached | `{"token":…,"expires_at":…,"permissions":{…},"repository_selection":…,"repositories":[names],"cached":bool,"account":…}` |
| `app-meta <slug>` | no | the JSON object from `GET /app` |
| `installations <slug>` | no | the JSON array from `GET /app/installations` (first page, `per_page=100`) |
| `delete <slug>` | **yes** | `{"slug":…,"deleted":true}` (removes key, meta, every token-cache item for the slug, and the prompt log) |

### Exit codes

| Code | `error` | Meaning |
|---|---|---|
| 2 | `usage` | bad argv |
| 3 | `not-found` | no key for this slug |
| 4 | `presence-denied` | the person cancelled, or presence is unavailable (`canEvaluatePolicy` false) |
| 5 | `rate-limited` | prompt budget spent; extra field `retry_after` (seconds) |
| 6 | `github` | HTTP or transport error; extra field `status` (HTTP code or 0) |
| 7 | `widened` | GitHub returned more than was requested; the token is discarded |
| 8 | `keychain` | keychain error, or an ambiguous lookup; extra field `osstatus` |
| 9 | `exists` | `import` without `--replace` when a key exists |
| 10 | `bad-key` | stdin is not a single unencrypted RSA private key of ≥ 2048 bits |
| 11 | `app-mismatch` | `GET /app` with the new key returned a different `id` than `app_id` |
| 1 | `internal` | anything else |

## 5. Behaviour

### Presence

`LAContext` with `.deviceOwnerAuthentication` (Touch ID, Apple Watch, or the password) and
`touchIDAuthenticationAllowableReuseDuration = 0`. A fresh context per prompt. macOS renders the prompt as
`"taaad-signer" <wants to> <reason>`, so reasons are verb phrases:

- import: `import the GitHub App private key for <slug> (App ID <n>)`
- replace: `replace the GitHub App private key for <slug> (App ID <n>)`
- delete: `delete the GitHub App private key for <slug>`
- mint: `mint a GitHub token for <slug> on <account> (repositories: <R>; permissions: <P>)`, where `<R>` is `all the App
  can reach` or a comma list and `<P>` is `all the App was granted` or `name:level` pairs sorted by name. Lists longer
  than 5 items show the first 5 and then `and N more`.

### `mint`, in this order

1. Validate argv. Load meta (`not-found` if absent) and the key (`keychain` if ambiguous).
2. Compute `narrowing_hash` = lowercase hex SHA-256 of the canonical JSON
   `{"permissions":{<sorted by name>},"repositories":[<lowercased, sorted, unique>]}` (no spaces).
3. **Cache:** if the cache item for `<slug>/<install_id>/<hash>` parses and `expires_at − now > 300 s`, print it with
   `"cached":true` and exit 0. There's no prompt, and the hit doesn't count against the rate limit. An unparseable item
   is deleted and treated as a miss.
4. Build the App JWT (see below) and `GET /app/installations/<install_id>` to obtain `account.login`. Errors give
   `github`.
5. **Rate limit:** load the prompt log; drop entries older than 600 s. If 3 or more remain, exit `rate-limited` with
   `retry_after = oldest + 600 − now`. An unparseable log counts as saturated: write `[now, now, now]` and exit
   `rate-limited`. Otherwise append `now` and **write the log before prompting**, so denials count.
6. Prompt. On cancel or failure: `presence-denied`, and nothing is sent to GitHub.
7. `POST /app/installations/<install_id>/access_tokens`. The body includes `"repositories":[…]` only if repositories
   were requested and `"permissions":{…}` only if permissions were requested; with neither, the body is empty.
8. **Widening check** (pure): if permissions were requested, every key in the response's `permissions` must be a
   requested name, at a level ≤ the requested one (`read < write < admin`). If repositories were requested,
   `repository_selection` must be `"selected"` and every returned name (lowercased) must be requested. A violation
   exits `widened`; the token is neither printed nor cached.
9. Write the cache item (replacing any old one), then print the result with `"cached":false`.

### `import`

1. Validate argv. Read stdin to EOF, rejecting input over 64 KiB. Without `--replace`, exit `exists` if a key for the
   slug exists.
2. Parse: exactly one PEM block, either `RSA PRIVATE KEY` (PKCS#1) or `PRIVATE KEY` (PKCS#8 wrapping rsaEncryption
   `1.2.840.113549.1.1.1`, unwrapped to PKCS#1 with a minimal DER reader). Encrypted PEMs, other key types, extra
   blocks and RSA keys under 2048 bits give `bad-key`. Create a `SecKey` with `SecKeyCreateWithData` (not yet stored).
3. Prompt (import or replace reason).
4. **Verify before storing:** sign a JWT with the in-memory key and `GET /app`. If the response's `id` ≠ `app_id`, exit
   `app-mismatch`. Nothing is stored and nothing is deleted.
5. With `--replace`: delete the old key, meta, the slug's token-cache items and the prompt log. Then add the key and the
   meta (`app_slug` from `GET /app`). If adding fails after the delete, exit `keychain`; the caller still holds the PEM
   file (`taaad rotate` shreds it only after success).

### App JWT

Header `{"alg":"RS256","typ":"JWT"}`, payload `{"iat":now−60,"exp":now+540,"iss":"<app_id as string>"}`, both
base64url without padding, compact JSON with fixed key order. Signature `SecKeyCreateSignature(.rsaSignatureMessagePKCS1v15SHA256)`.

### HTTP

`URLSession(configuration: .ephemeral)`, timeout 15 s, base `https://api.github.com`. Headers:
`Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `User-Agent: taaad-signer/<version>`,
`Authorization: Bearer <JWT>`. A non-2xx response gives `github` with the status; the message includes GitHub's
`message` field, never the request headers.

## 6. Code structure

Swift Package Manager package in `signer/`:

- `SignerCore` (library): **pure** code plus protocols. It contains argv parsing and validation, the canonical
  narrowing and its hash, the widening check, cache freshness, the rate-limit decision, reason text, JWT
  header/payload encoding, PEM/PKCS#8 parsing, and the command orchestration (`mint`, `import`, `delete`, `status`)
  written against protocols:
  `Store` (get/put/delete/list-by-prefix for the four item kinds), `KeyStore` (create from PKCS#1 DER, add, find, delete,
  sign), `Clock`, `Presence` (`approve(reason) -> Bool`), `GitHub` (`get(path, jwt)`, `post(path, jwt, body)`).
- `taaad-signer` (executable): the shell. It holds the keychain-backed `Store`/`KeyStore`, the `LAContext` presence,
  the `URLSession` client and `main`. It contains no decisions.
- `SignerCoreTests` (XCTest): fakes for every protocol. Seeded randomised tests (fixed seeds, no dependencies) stand in
  for property tests.

### Tests that must fail when the code is wrong

- Cache hit: no prompt, no rate-log write, no POST. An expired entry (≤ 300 s left) is a miss.
- Rate limit: the 4th prompt within 600 s is refused without prompting; a denied prompt still counts; a corrupt log is
  saturated.
- Ordering: denied presence → no POST; `GET installation` failure → no prompt.
- Widening (randomised): any response permission not requested, or at a higher level, or any unrequested repository,
  or `repository_selection: "all"` when repositories were requested → `widened`, nothing cached.
- Import: `app-mismatch` → store unchanged; `--replace` deletes the old items only after verification succeeds; PKCS#1
  and PKCS#8 parse to the same DER; an encrypted, a non-RSA, a 1024-bit key and two keys are each `bad-key`.
- JWT: header and payload bytes are exact for a fixed clock; the signature verifies with the public key (test key
  generated in the test, never a real one).
- Output: no error path writes to stdout; the token never appears on stderr.

## 7. Build and install

- `signer/scripts/build.sh`: `swift build -c release`, assemble the bundle under `signer/build/`, copy the profile from
  `$TAAAD_SIGNER_PROFILE`, sign with `$TAAAD_SIGN_IDENTITY` (a SHA-1 or a name), then verify with
  `codesign --verify --strict`. The script also checks that the designated requirement equals §1, that the
  entitlements equal §1 exactly, and that the profile's App ID and team match. It **refuses to sign** if any check
  fails.
- `signer/scripts/install.sh` (run with sudo): `ditto` the bundle to `/Library/Application Support/taaad/`,
  `chown -R root:wheel`, then verify the installed copy again.
- Notarisation and a signed `.pkg` for other people are a later step: they need the Developer ID Installer
  certificate and notarytool credentials. A locally built, never-quarantined bundle runs without notarisation.

## 8. Python side (`src/taaad`)

- `acl.py`: add `SIGNER_CSREQ`, the requirement without `/* exists */` comments, for `codesign --verify -R=`.
- `signer.py`: typed subprocess boundary.
  - `SIGNER_PATH` constant (§1); `available()` = macOS and the executable exists.
  - It runs the helper with argv only, `env={"PATH": "/usr/bin:/bin"}`, `stdin` only for `import`, a timeout of 120 s
    for commands that prompt and 30 s otherwise, and parses the single JSON line.
  - Exit codes map to typed exceptions under `SignerError`: `SignerMissing`, `KeyNotFound`, `PresenceDenied`,
    `RateLimited(retry_after)`, `GitHubError(status)`, `WidenedToken`, `KeychainError`, `KeyExists`, `BadKey`,
    `AppMismatch`. Exception messages never contain stdout.
  - Functions: `has_key(slug)`, `status(slug)`, `import_key(slug, app_id, pem: str, replace: bool)`,
    `mint(slug, install_id, repos=(), perms={}) -> MintedToken` (frozen dataclass), `app_meta(slug)`,
    `installations(slug)`, `delete(slug)`, and `verify_installed() -> str | None` (None when
    `codesign --verify --strict -R=<SIGNER_CSREQ>` passes on the bundle, otherwise the reason).
  - `mint` repeats the widening check on the helper's output (defence in depth) and raises `WidenedToken`.
- `keystore.py`: the single routing point. Signer when `sys.platform == "darwin"` **and** `signer.available()` **and**
  `signer.has_key(slug)`; legacy `secrets`/`github` otherwise. **A signer error never falls back to the legacy path.**
  Functions: `mint(cfg) -> str`, `app_meta(cfg) -> dict`, `installations(cfg) -> list[dict]`,
  `store_new(slug, app_id, pem, keychain_key)`, `has_key(cfg)`, `describe(cfg) -> str`
  (`taaad-signer` / `keychain (readable by Python)` / `missing`), `delete(cfg)` (both stores).
- Call sites: `identity.mint_token` → `keystore.mint`; `install` → `keystore.installations` (the account match becomes
  a pure function over the list); `register` and `rotate` → `signer.import_key` on macOS when the helper is installed,
  otherwise `keyring` plus the existing notice; `apps show` → `keystore.describe`; `apps remove` and `uninstall --purge`
  → `keystore.delete`.
- `rotate` on the signer path: `import_key(replace=has_key)` → if `install_id`: `mint` (proves the installation) →
  delete the legacy keyring item if present → shred the PEM file → revoke message. Any failure leaves the file in
  place.
- `doctor` on macOS: report the helper missing or not verifying (`verify_installed`) as FAIL. Per App: on the signer
  path, `[ok] key held by taaad-signer`; a legacy item that still exists is a FAIL ("legacy copy still readable by
  Python; `taaad rotate` removes it"); the key check is `app_meta` (no prompt) instead of a mint. Without a signer key,
  run the Phase 1 ACL check as today.
- Tests use a **fake helper**: a generated executable script at a temporary `SIGNER_PATH` that implements §4 from a
  JSON scenario file whose path is baked into the script (the helper gets no environment). Cover: the routing rules;
  `PresenceDenied` with `keyring` patched to raise if touched; a widened fake response refused by Python; each exit
  code → exception; no token in any exception text; `doctor` flagging a leftover legacy item.

## 9. Review fixes (2026-11-08)

These override the sections above where they conflict. Source: the independent security review of the first build.

1. **One prompt budget for every prompt** (`import`, `replace`, `delete`, `mint`). Each counts against the slug's prompt
   log (3 per 600 s) and is written before prompting. The decision is the existing `RateLimit` logic.
2. **Serialise the budget.** The rate check, the log write and the prompt run under an exclusive `flock` on a
   read-only file descriptor of the helper's own executable (the path from `Bundle.main.executablePath`, falling back
   to `_NSGetExecutablePath`). The lock is released right after the prompt returns. A lock that can't be taken within
   120 s gives `rate-limited` with `retry_after = 60`. The core expresses this as a protocol (`Lock` with
   `withExclusive { … }`) so tests can prove that two interleaved `Signer` runs never exceed the budget.
3. **`import --replace` must keep the App ID.** If a meta item exists and its `app_id` ≠ the requested `app_id`,
   exit `app-mismatch` **before prompting**. Rotation never changes the App ID.
4. **Implicit `metadata: read`.** When permissions are requested, a `metadata: read` entry in the response is allowed
   even if it was not requested. `metadata` at `write` or higher still counts as widening.
5. **JWT lifetime:** `exp = now + 120` (with `iat = now − 60`). The helper uses each JWT immediately and makes a fresh
   one after the prompt.
6. **Prompt text:** permissions before repositories:
   `mint a GitHub token for <slug> on <account> (permissions: <P>; repositories: <R>)`. Each shown repository name is cut
   to 40 characters, with `…` marking a cut.
7. **`install.sh`:** refuse a source bundle containing anything other than regular files and directories (`find ! -type f
   ! -type d`). Copy with `cp -R`, not `ditto`. Afterwards assert that the installed tree has no symlinks and that every
   entry is owned by `root:wheel` without group or other write; then verify the signature.
8. **`build.sh` adapter guard:** fail the build unless every `SecItemAdd`, `SecItemUpdate`, `SecItemDelete` and
   `SecItemCopyMatching` call in `Sources/taaad-signer/Keychain.swift` takes its query from `KC.query(` (compare the
   counts).
9. **Python `signer._run` checks the path before every call** (`os.lstat`, never following links): `SIGNER_PATH` must
   be a regular file (not a symlink), owned by uid 0, without group or other write. Each parent directory up to and
   including `/Library` must be owned by uid 0 and must not be writable by group or other. Any failure raises
   `SignerMissing` with the reason. Tests need an injectable path check, since a test can't create root-owned files.
10. **Python widening check:** the same implicit `metadata: read` rule as item 4.
11. **`register` failure message** also says that the private key generated during registration is live on GitHub but
    held by nobody, and must be deleted on the App's settings page (Private keys, the key dated today).
12. **New helper command `installation <slug> <install_id>`** (no presence prompt): prints the JSON object from
    `GET /app/installations/<install_id>` (it contains `account.login` and the granted `permissions`). Same errors as
    `app-meta`. `taaad mint` needs it to compute its permission body before asking for a token.
13. **`taaad mint` (src/taaad/commands/mint.py, merged on main as 4a642df) goes through the helper** when
    `keystore.uses_signer(slug)`: `signer.installation(...)` for the owner check and the granted permissions, then
    `signer.mint(slug, install_id, repos=[repo], perms=body)`. The command's contract stays the same: argv, TTY
    refusal, the stdout JSON keys (`token, expiresAt, installationAccount, installationId, appId, repositories,
    permissions`), empty stdout on any refusal, and all of its existing checks (exactly one repository, no permission
    above the request). Without the helper it keeps today's legacy path. Route it through `keystore` (add
    `keystore.installation(cfg)` and `keystore.mint_scoped(cfg, repo, perms) -> MintedToken-like`), never by calling
    `secrets` directly.
14. **Stable exit codes for `taaad mint` refusals** (consumer: repo-aegis): `2` TTY refusal (unchanged), `3`
    rate-limited (`signer.RateLimited`), `4` presence cancelled or denied (`signer.PresenceDenied`), and `1` for
    everything else. Stdout stays empty on all of them. Document them in `taaad mint --help` and README.

### Release review (round 3, before v0.6.0)

Independent security review of the round-2 changes. Fixed:

- **L1** `PROMPT_TIMEOUT_S` raised to 240 s so Python never kills the helper while it waits for the 120 s budget lock; a held lock now surfaces as `rate-limited`.
- **L2** `install.sh` `assert_tree` rejects any entry that is not a regular file or directory (FIFOs and sockets created after the source check).
- **L3** `install.sh` refuses a symlinked destination directory.
- **L4** `import --replace` on a key with no meta item fails with a `keychain` error ("delete it first"), so the App ID pin cannot be skipped.
- `package.sh`: see RELEASING.md (staged payload has no extended attributes, package is asserted not relocatable).

**M1 accepted** (documented in SECURITY.md): the App JWT for the read commands and the first `mint` request is sent before any prompt, over a session that honours system proxies and user- or admin-installed trust anchors. An attacker with administrator rights could intercept a 120 s JWT and mint tokens without presence. Pinning to the system roots would break the helper behind corporate TLS-inspection proxies, so it is not done in 0.1.0. Revisit if a deployment needs it (option A in plans/0003-pem-keychain-acl.md). The unprompted read commands (`installation`, `app-meta`, `installations`) are callable by any local process, as stated in section 4.
