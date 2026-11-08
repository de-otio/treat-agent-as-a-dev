# Automating the taaad-signer release in CI

Status: **design, not yet implemented or tested.** Nothing in `.github/workflows/` does this yet. The workflows below
are starting points; build them in a branch and run them once before relying on them.

Audience: maintainers. Read `RELEASING.md` first; this document assumes its manual process and says what changes.

## Summary and recommendation

Automate in two tiers, and treat the second as a decision, not a default.

| Tier | What runs | Secrets in CI | Effect on the trust root |
|---|---|---|---|
| **1. Verify** (do now) | Swift and Python tests, unsigned build, script lint, version-consistency check, on every pull request | none | none |
| **2. Release** (decide) | Build, sign, notarise, staple, attest, on an approved version tag | signing certificates, provisioning profile, notary key | **moves the root of trust** from the maintainer's release keychain to GitHub repository administration |
| 3. Self-hosted runner | same as 2, on a Mac you own | on that Mac | recreates the "identity in a keychain an agent can reach" problem. GitHub: self-hosted runners "should almost never be used for public repositories"; **not recommended** |

Why Tier 2 is a decision: SECURITY.md says anything signed with the Developer ID **and** the helper's profile gets every
App key on every machine that runs it. Today that capability needs a password that only the maintainer types. In CI it
needs the ability to run a workflow in a protected environment. Everyone who can change what that workflow runs, or
who can approve it, becomes part of the trust root. For a single maintainer, **keep releasing locally** (the process in
`RELEASING.md` is short and now tested) and adopt Tier 2 only when there is a second human who reviews releases, or the
release cadence makes the manual process the bigger risk.

## Tier 1: verify on every pull request (no secrets)

Everything that does not need a key should run in CI. This catches broken builds, the version-bump mistake, and script
regressions before a release day. It holds no secrets, so it is safe for `pull_request` from forks.

`.github/workflows/signer-ci.yml`:

```yaml
name: signer-ci

on:
  pull_request:
    paths: ["signer/**", ".github/workflows/signer-ci.yml"]
  push:
    branches: [main]
    paths: ["signer/**", ".github/workflows/signer-ci.yml"]

permissions:
  contents: read

jobs:
  signer:
    runs-on: macos-15 # pin an image; do not use macos-latest for anything that signs. Check the current labels.
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Toolchain (recorded in the log)
        run: |
          xcodebuild -version
          swift --version
      - name: Swift tests
        working-directory: signer
        timeout-minutes: 15
        run: swift test
      - name: Unsigned build (needs neither profile nor identity)
        working-directory: signer
        timeout-minutes: 15
        run: ./scripts/build.sh --no-sign
      - name: Script syntax
        working-directory: signer
        run: |
          for f in scripts/*.sh; do bash -n "$f"; done
      - name: Version consistency (Info.plist and SignerInfo.version must match)
        working-directory: signer
        run: |
          plist="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' Resources/Info.plist)"
          src="$(sed -n 's/.*static let version = "\(.*\)".*/\1/p' Sources/SignerCore/Errors.swift)"
          [ -n "$src" ] && [ "$plist" = "$src" ] || { echo "version mismatch: plist=$plist source=$src"; exit 1; }
```

Also in this tier, and separate from the signer: fix `ci.yml` (it predates these rules). It has no `timeout-minutes`, no
`permissions:` block, and uses `actions/checkout@v4` and `setup-python@v5`, which are Node 20 runtimes. The suite runs on
macOS with the test guard in `tests/conftest.py`, so it can never reach a helper on the runner.

## Tier 2: signed release in CI

### Threat model: what changes

Locally, the signing identity sits in a password-locked keychain and the agent and the process are kept apart. In CI:

- The secrets are readable by **any step of a job that names the environment**, including a step added by a pull
  request that was merged carelessly, or by a compromised action. Mitigation: environment secrets only, a signing job
  with no third-party actions, every action pinned by commit SHA, and approval on the environment.
- The workflow file that runs is the one **at the tagged commit**. Whoever can push a matching tag can run a modified
  workflow with the secrets. Mitigation: a tag ruleset (who may create `signer-v*`) plus an environment deployment rule
  that only allows those tags.
- **A single maintainer cannot give themselves separation of duties.** "Required reviewers" set to yourself is a speed
  bump, not a control. It becomes a control with a second person, and with *Prevent self-review* switched on.
- Fork pull requests must never reach the secrets: trigger on **tags only**, never `pull_request_target`.
- The provisioning profile is **not secret** (it is embedded in the shipped bundle and anyone can extract it). The
  only secrets that matter are the private keys. But every certificate listed in the profile is a root of trust, so list
  only the ones you mean to.
- A leaked signing key is revoked on developer.apple.com. Binaries signed earlier stay valid unless Apple revokes them,
  so a leak is a rotate-everything event (see "If a key leaks").

### Prerequisites (one-time)

1. **A CI-only Developer ID Application certificate**, created from its own certificate request, with its own private
   key. Do not upload the maintainer's local identity: separate keys can be revoked separately. Apple limits how many
   Developer ID certificates an account may hold; check before creating one.
2. **Regenerate the provisioning profile to include both certificates.** The profile names the certificates allowed to
   use it (Apple, TN3125: every profile has a `DeveloperCertificates` property holding the certificates of each
   developer who can sign code the profile covers), so the CI certificate will not work with the current profile. `build.sh` already refuses to sign unless the
   profile matches the signing certificate. The designated requirement pins the team, not one certificate, so
   installed copies keep working.
3. **A Developer ID Installer certificate** for CI, likewise separate.
4. **A notarisation credential.** Prefer an **App Store Connect API key** to the Apple ID password: it has its own
   revocation and is not tied to a person's login. Prefer a **Team key** (created by an Admin; it needs `--issuer`).
   Apple's sources disagree about *individual* keys: the App Store Connect API article says individual keys cannot use
   `notaryTool`, while the `notarytool` man page documents them ("do not provide `--issuer` for Individual keys, this will
   result in a 401") and the Electron notarize docs say they work only with Xcode 26 or later. If a team key is not
   available to you, test an individual key against the runner's Xcode before relying on it. Apple does not name a
   minimum role for notarisation (Electron's docs say App Manager); use the least role that works and verify it with a
   real run. The private key downloads **once**; keep a copy in your password manager.
5. **A GitHub environment** `signer-release` with: required reviewers (a second person), *Prevent self-review*, a
   deployment rule allowing only tags `signer-v*`, and all secrets below stored as *environment* secrets (not repository
   secrets). **Switch off administrator bypass** for the environment: GitHub lets administrators bypass protection
   rules by default, which would undo the approval for exactly the person you are guarding against. Also set the
   repository's default `GITHUB_TOKEN` permission to read-only. (GitHub: required reviewers work on a free **public**
   repository, up to six, and one approval is enough; the tag rule is matched against `GITHUB_REF`, and `*` does not
   match `/`.)
6. **A tag ruleset** for `signer-v*`: only maintainers can create; no deletion; no updates.
7. **CODEOWNERS** with the maintainers for `.github/workflows/**`, `signer/**` and `src/taaad/signer.py`, with branch
   protection requiring a code-owner review on `main`.
8. **Dependabot** for `github-actions`, so pinned SHAs get updated by reviewed pull requests. GitHub's secure-use
   guide now also describes action lockfiles, which it says supersede SHA pinning for repositories that adopt them;
   SHA pins are what this document uses, and a lockfile is a later improvement. Check each SHA belongs to the action's
   own repository, not a fork.

### Secrets (environment `signer-release`)

| Secret | Contents |
|---|---|
| `SIGNER_APP_P12_B64`, `SIGNER_APP_P12_PASSWORD` | CI Developer ID Application identity (`base64` of the `.p12`) and its export password |
| `SIGNER_INSTALLER_P12_B64`, `SIGNER_INSTALLER_P12_PASSWORD` | CI Developer ID Installer identity |
| `SIGNER_PROFILE_B64` | the regenerated provisioning profile |
| `NOTARY_KEY_P8_B64`, `NOTARY_KEY_ID`, `NOTARY_ISSUER_ID` | App Store Connect API key (`NOTARY_ISSUER_ID` only for team keys) |

Expose each secret only to the step that needs it, through that step's `env:`. Never at job or workflow level, and
never on a command line other than the throwaway keychain password.

### The workflow

`.github/workflows/signer-release.yml` (the version in `Info.plist` must already be bumped and merged to `main`):

```yaml
name: signer-release

on:
  push:
    tags: ["signer-v*"]

permissions: {} # nothing by default; the job asks for exactly what it needs

concurrency:
  group: signer-release
  cancel-in-progress: false

jobs:
  release:
    runs-on: macos-15 # pinned image
    timeout-minutes: 60
    environment: signer-release # required reviewers; secrets are released only after approval
    permissions:
      contents: read
      id-token: write # to sign the provenance attestation
      attestations: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Tag must be on main and match the version
        run: |
          set -euo pipefail
          git fetch --no-tags origin main
          git merge-base --is-ancestor "$GITHUB_SHA" origin/main || { echo "tag is not on main"; exit 1; }
          v="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' signer/Resources/Info.plist)"
          [ "$GITHUB_REF_NAME" = "signer-v$v" ] || { echo "tag $GITHUB_REF_NAME does not match version $v"; exit 1; }

      - name: Tests (no secrets in this step)
        working-directory: signer
        timeout-minutes: 15
        run: swift test

      - name: Throwaway keychain with the signing material
        env:
          APP_P12_B64: ${{ secrets.SIGNER_APP_P12_B64 }}
          APP_P12_PASSWORD: ${{ secrets.SIGNER_APP_P12_PASSWORD }}
          INSTALLER_P12_B64: ${{ secrets.SIGNER_INSTALLER_P12_B64 }}
          INSTALLER_P12_PASSWORD: ${{ secrets.SIGNER_INSTALLER_P12_PASSWORD }}
        run: |
          set -euo pipefail
          KC="$RUNNER_TEMP/taaad-ci.keychain-db"
          KC_PW="$(openssl rand -hex 24)"
          echo "::add-mask::$KC_PW"
          security create-keychain -p "$KC_PW" "$KC"
          security set-keychain-settings -lut 3600 "$KC"
          security unlock-keychain -p "$KC_PW" "$KC"
          printf '%s' "$APP_P12_B64" | base64 --decode > "$RUNNER_TEMP/app.p12"
          printf '%s' "$INSTALLER_P12_B64" | base64 --decode > "$RUNNER_TEMP/installer.p12"
          security import "$RUNNER_TEMP/app.p12" -k "$KC" -P "$APP_P12_PASSWORD" -T /usr/bin/codesign
          security import "$RUNNER_TEMP/installer.p12" -k "$KC" -P "$INSTALLER_P12_PASSWORD" -T /usr/bin/productbuild -T /usr/bin/productsign
          # No GUI on a runner: without this, codesign cannot use the key (errSecInternalComponent).
          security set-key-partition-list -S apple-tool:,apple: -s -k "$KC_PW" "$KC" >/dev/null
          rm -f "$RUNNER_TEMP/app.p12" "$RUNNER_TEMP/installer.p12"
          echo "TAAAD_SIGN_KEYCHAIN=$KC" >> "$GITHUB_ENV"

      - name: Notarisation profile in that keychain
        env:
          NOTARY_KEY_P8_B64: ${{ secrets.NOTARY_KEY_P8_B64 }}
          NOTARY_KEY_ID: ${{ secrets.NOTARY_KEY_ID }}
          NOTARY_ISSUER_ID: ${{ secrets.NOTARY_ISSUER_ID }}
        run: |
          set -euo pipefail
          printf '%s' "$NOTARY_KEY_P8_B64" | base64 --decode > "$RUNNER_TEMP/notary.p8"
          xcrun notarytool store-credentials taaad-notary \
            --key "$RUNNER_TEMP/notary.p8" --key-id "$NOTARY_KEY_ID" ${NOTARY_ISSUER_ID:+--issuer "$NOTARY_ISSUER_ID"} \
            --keychain "$TAAAD_SIGN_KEYCHAIN"
          rm -f "$RUNNER_TEMP/notary.p8"

      - name: Build and sign
        working-directory: signer
        timeout-minutes: 20
        env:
          SIGNER_PROFILE_B64: ${{ secrets.SIGNER_PROFILE_B64 }}
        run: |
          set -euo pipefail
          printf '%s' "$SIGNER_PROFILE_B64" | base64 --decode > "$RUNNER_TEMP/signer.provisionprofile"
          TAAAD_SIGN_IDENTITY="Developer ID Application" \
          TAAAD_SIGNER_PROFILE="$RUNNER_TEMP/signer.provisionprofile" \
          ./scripts/build.sh

      - name: Package, notarise, staple
        working-directory: signer
        timeout-minutes: 30 # notarisation normally takes minutes; --wait blocks until Apple answers
        env:
          TAAAD_INSTALLER_IDENTITY: Developer ID Installer
          TAAAD_NOTARY_PROFILE: taaad-notary
        run: ./scripts/package.sh

      - name: Attest provenance of the final package
        uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4.2.2
        with:
          subject-path: signer/build/taaad-signer-*.pkg

      - name: Upload the package and its checksum
        run: |
          set -euo pipefail
          cd signer/build
          shasum -a 256 taaad-signer-*.pkg > SHA256SUMS
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: taaad-signer-pkg
          path: |
            signer/build/taaad-signer-*.pkg
            signer/build/SHA256SUMS
          if-no-files-found: error
          retention-days: 30

      - name: Remove the keychain and key files
        if: always()
        run: |
          security delete-keychain "$RUNNER_TEMP/taaad-ci.keychain-db" || true
          rm -f "$RUNNER_TEMP"/*.p12 "$RUNNER_TEMP"/*.p8 "$RUNNER_TEMP"/*.provisionprofile
```

Design choices behind it:

- **One job does build, sign, package and notarise.** Passing a signed bundle between jobs through an artifact would be
  an unneeded swap window. Only the finished package leaves the job.
- **The secret-holding job cannot publish.** It has `contents: read`, no write token. A human downloads the artifact,
  verifies it, and runs `gh release create` (below). Publishing stays a deliberate, confirmed act, as in `RELEASING.md`.
- **`package.sh` is unchanged.** It already reads the keychain and the notary profile from the environment, which is
  why the CI step only has to fill them. All of its checks (strict requirement, hardened runtime, timestamp, payload
  contents, "Accepted", staple, Gatekeeper) run in CI as well.
- **Official `actions/*` only**, pinned by commit SHA with the version in a comment, all `node24`. No marketplace
  signing or notarisation actions: each would hold the keys.
- **The password for the throwaway keychain is random per run and masked.** Putting it on a command line is
  acceptable here only because the runner is ephemeral. Do not copy the pattern to a persistent machine.

### Publishing from a verified artifact (human, local)

```sh
gh run download <run id> --repo de-otio/treat-agent-as-a-dev --name taaad-signer-pkg --dir ./dl
shasum -a 256 -c ./dl/SHA256SUMS
gh attestation verify ./dl/taaad-signer-<version>.pkg --repo de-otio/treat-agent-as-a-dev
pkgutil --check-signature ./dl/taaad-signer-<version>.pkg
spctl --assess --type install -vv ./dl/taaad-signer-<version>.pkg
```

All four must pass. Then attach the package to a GitHub release with its SHA-256 in the notes (confirm repo, tag and file
before uploading, as in `RELEASING.md`).

### Caveats

- **Builds are not reproducible.** Signatures carry timestamps and notarisation is Apple's. The substitutes are the
  provenance attestation (which workflow, which commit), the SHA-256, and Apple's notarisation ticket. The toolchain
  version is recorded in the log, so pin the runner image.
- **Hosted macOS runners may change Xcode between images.** A new Xcode can change the produced binary or the
  notarisation result. Update the image label deliberately, in a reviewed pull request, and run Tier 1 on it first.
- **Notarisation is a network dependency.** Apple's service can be slow or down; the job's timeout turns a hang into a
  failure, and a failed run can be re-run from the Actions page after fixing the cause.
- **The keychain recipe is practice, not Apple documentation.** Creating a throwaway keychain, importing the `.p12`
  and calling `security set-key-partition-list -S apple-tool:,apple:` is the sequence in GitHub's own macOS signing
  guide, and the same call is the community fix for `errSecInternalComponent` on CI. Apple's keychain partitions
  are poorly documented (an Apple DTS engineer calls them "a bit of a dark art" with "minimal" docs, and names the
  data protection keychain as the long-term path). Treat the recipe as likely to need adjusting on a new macOS image.
  GitHub's guide also imports with `-A` (any application); this document uses `-T` for the specific tools instead.
- Mask every value **derived** from a secret, not only the secrets themselves (GitHub: register derived values such as
  a base64 or URL-encoded copy with `::add-mask::`), and never store structured data (JSON, YAML) as one secret.
- **Not tested.** The keychain-import recipe, the partition-list step and `notarytool store-credentials --key` have
  not been run against these scripts. Do a dry run with `--no-notarize` first (set `TAAAD_NOTARY_PROFILE` only for
  the real run).

### If a key leaks

1. Revoke the certificate on developer.apple.com, revoke the notary API key in App Store Connect, and delete the
   environment's secrets. Apple's notary service keeps an audit trail of software distributed with your signing key;
   if you find unauthorised versions, you can work with Apple to revoke their tickets.
2. Rotate the affected certificate, regenerate the profile, and ship a new release signed with the remaining or new
   identity.
3. Treat every installed helper signed by the leaked identity as suspect, and rotate each App's key (RUNBOOK Step 9).
   The helper's designated requirement pins the team, not one certificate, so an attacker with the leaked key can sign a
   binary that satisfies it. That is the reason the CI certificate is separate from the local one: you can revoke one
   without discarding the other.

## Checklist before turning Tier 2 on

- [ ] A second human is a required reviewer, with *Prevent self-review* on.
- [ ] Tag ruleset for `signer-v*` and an environment tag rule are in place.
- [ ] CODEOWNERS covers `.github/workflows/**` and `signer/**`; `main` requires a code-owner review.
- [ ] Separate CI Developer ID Application and Installer certificates; profile regenerated to list them.
- [ ] Notary credential is an API key, not an Apple ID password.
- [ ] Every action is pinned by SHA; Dependabot for `github-actions` is on.
- [ ] A dry run of the workflow with `--no-notarize` has produced a package that `package.sh` accepted.
- [ ] One real tagged run was approved, downloaded, and verified end to end with the commands above.
- [ ] SECURITY.md states the new trust root (repository administration and the environment).

## Sources

Apple:
- [Customizing the notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow) and [Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- [TN3147: Migrating to the latest notarization tool](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool) (API key and `store-credentials`; the data protection keychain is the default unless `--keychain` is given)
- [Packaging Mac software for distribution](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution) and [Creating distribution-signed code for macOS](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac) (outermost container only; Installer identity; restricted entitlements need a profile)
- [TN3125: Inside Code Signing: Provisioning Profiles](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles)
- [Creating API keys for App Store Connect API](https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api)
- [notarytool man page (mirror)](https://keith.github.io/xcode-man-pages/notarytool.1.html)
- Apple Developer Forums: [why `set-key-partition-list` is needed](https://developer.apple.com/forums/thread/666107), [valid values](https://developer.apple.com/forums/thread/756171)

GitHub:
- [Signing Xcode applications](https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/sign-xcode-applications)
- [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)
- [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)

Other: [Electron notarize](https://packages.electronjs.org/notarize) (individual keys and Xcode 26; App Manager role).

Not found in primary sources, kept as stated from general GitHub behaviour: a push-triggered workflow runs from the pushed
commit, so a tag that carries a modified workflow runs the modified file.
