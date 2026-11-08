# Building, signing and installing taaad-signer (maintainer)

Anything signed with the taaad Developer ID **and** the helper's provisioning profile gets every App key on every
machine that runs it (SECURITY.md, "macOS keychain access list"). So the signing identity is the root of trust, and
this document is mostly about keeping it out of an agent's reach.

## Rules

- Build and sign only from a terminal you opened yourself, with **no agent session running** on the machine. A
  running agent could change the sources or swap the binary between build and sign.
- The Developer ID Application identity lives **only** in a separate, password-locked keychain, never in the login
  keychain. Anything in the login keychain is one "Allow" click away from an agent.
- The provisioning profile is not secret, but it names the maintainer, so it is never committed. Keep it outside the
  repo and pass it in through `TAAAD_SIGNER_PROFILE`.

## One-time: a release keychain for the Developer ID identity

You need the password-protected `.p12` backup of the Developer ID Application identity.

```sh
# 1. Create the keychain. macOS asks for a new password; use one that differs from your login password.
security create-keychain -P taaad-release.keychain-db

# 2. Lock it on sleep and after 5 minutes idle.
security set-keychain-settings -l -u -t 300 taaad-release.keychain-db

# 3. Import the identity from the .p12 backup (macOS asks for the .p12 password).
security import /path/to/DeveloperID-Application.p12 -k taaad-release.keychain-db -T /usr/bin/codesign

# 4. Check: the identity is listed in the release keychain.
security find-identity -v -p codesigning taaad-release.keychain-db

# 5. Check that signing works and asks for the release-keychain password while the keychain is locked.
security lock-keychain taaad-release.keychain-db
cp /usr/bin/true /tmp/sign-test
codesign -s "Developer ID Application" --keychain ~/Library/Keychains/taaad-release.keychain-db -f /tmp/sign-test
rm /tmp/sign-test
```

6. Only after step 5 works: open **Keychain Access**, select **login**, then **My Certificates**, right-click
   **Developer ID Application: … (U26DY5A9DU)** and choose **Delete**. That deletes the certificate together with its
   private key, from the login keychain only.
7. Check: `security find-identity -v -p codesigning login.keychain-db` no longer lists the Developer ID Application
   identity.

The keychain may stay in the search list. While it is locked, nothing can use the identity without its password.

## Build and sign

```sh
cd signer
TAAAD_SIGN_IDENTITY="Developer ID Application" \
TAAAD_SIGN_KEYCHAIN=~/Library/Keychains/taaad-release.keychain-db \
TAAAD_SIGNER_PROFILE=/path/to/taaad-signer_Developer_ID.provisionprofile \
./scripts/build.sh
```

`build.sh` refuses to sign unless the profile's App ID, team and certificate match. After signing it checks that the
designated requirement and the entitlements are exactly those in SPEC.md §1. The signed bundle lands in
`signer/build/taaad-signer.app`.

## Install

```sh
sudo ./scripts/install.sh
```

This copies the bundle to `/Library/Application Support/taaad/taaad-signer.app`, sets every entry to `root:wheel`,
refuses symlinks, and verifies the installed signature. Then check with `taaad doctor`.

## Not yet: notarisation and a signed .pkg

These are only needed to give the helper to other people. They need a **Developer ID Installer** certificate and
`notarytool` credentials (an App Store Connect API key, or an app-specific password stored with
`xcrun notarytool store-credentials`). A bundle built and installed on the same Mac is never quarantined and runs
without notarisation.
