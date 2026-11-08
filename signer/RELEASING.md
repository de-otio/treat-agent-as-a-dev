# Building, signing and installing taaad-signer (maintainer)

Anything signed with the taaad Developer ID **and** the helper's provisioning profile gets every App key on every
machine that runs it (SECURITY.md, "macOS keychain access list"). So the signing identity is the root of trust, and
this document is mostly about keeping it out of an agent's reach. Users who only install the helper need just the last section, "Installing from a release".

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

## One-time: Developer ID Installer certificate

The `.pkg` is signed with a separate **Developer ID Installer** identity. It's a different certificate from the
Developer ID Application identity that signs the code.

1. Create a certificate signing request. In **Keychain Access**, choose **Keychain Access ▸ Certificate Assistant ▸
   Request a Certificate From a Certificate Authority**. Enter your email, select **Saved to disk**, and save the
   `.certSigningRequest` file.
2. On developer.apple.com, go to **Certificates ▸ +**, choose **Developer ID Installer** (profile type "G2 Sub-CA"),
   upload the request and download the `.cer`.
3. Double-click the `.cer`; it pairs with the private key that step 1 created in the **login** keychain. Export the
   pair as a password-protected `.p12` (in Keychain Access: **My Certificates ▸ Developer ID Installer: … ▸ Export**),
   keep the `.p12` offline, and import it into the release keychain:
   ```sh
   security import /path/to/DeveloperID-Installer.p12 -k taaad-release.keychain-db -T /usr/bin/productbuild -T /usr/bin/productsign
   ```
4. Delete the Installer identity from the login keychain (as in step 6 above), then check. Leave out
   `-p codesigning`, because that filter hides installer identities:
   ```sh
   security find-identity -v taaad-release.keychain-db     # lists "Developer ID Installer: … (U26DY5A9DU)"
   ```

## One-time: notarisation credentials

`notarytool` needs an Apple ID **app-specific password** or an **App Store Connect API key**. The password is
simpler for one maintainer:

1. At account.apple.com go to **Sign-In and Security ▸ App-Specific Passwords ▸ +** and name it `taaad notarytool`.
2. Store it **in the release keychain**, not the default data-protection keychain, so it locks with the signing
   identities:
   ```sh
   xcrun notarytool store-credentials taaad-notary \
     --apple-id <your Apple ID> --team-id U26DY5A9DU \
     --keychain ~/Library/Keychains/taaad-release.keychain-db
   ```
   It asks for the app-specific password and checks it before saving.

## Each release: package, notarise, staple

```sh
cd signer
# 1. Build and sign the bundle (it must carry a secure timestamp and the hardened runtime; build.sh does both).
TAAAD_SIGN_IDENTITY="Developer ID Application" \
TAAAD_SIGN_KEYCHAIN=~/Library/Keychains/taaad-release.keychain-db \
TAAAD_SIGNER_PROFILE=/path/to/taaad-signer_Developer_ID.provisionprofile \
./scripts/build.sh

# 2. Package, sign, notarise and staple.
TAAAD_INSTALLER_IDENTITY="Developer ID Installer" \
TAAAD_SIGN_KEYCHAIN=~/Library/Keychains/taaad-release.keychain-db \
TAAAD_NOTARY_PROFILE=taaad-notary \
./scripts/package.sh
```

`package.sh` refuses to continue unless:

- the bundle verifies strictly against the taaad designated requirement, with the hardened runtime and a secure
  timestamp, and contains no symlinks;
- `pkgutil --check-signature` reports **"Signed with a trusted timestamp"** and **"1. Developer ID Installer: …
  (U26DY5A9DU)"**.

The package installs exactly one thing, `/Library/Application Support/taaad/taaad-signer.app`, owned by
`root:wheel`, with no install scripts. The bundle is marked **not relocatable**, so Installer never "upgrades" a copy
somewhere else on disk.

The script submits the `.pkg` with `notarytool submit --wait` (only the outermost container is notarised) and fails
unless the status is `Accepted`. It then staples the ticket (`stapler staple`, then `stapler validate`), checks
Gatekeeper's view with `spctl --assess --type install`, and prints the package's SHA-256. If notarisation fails, read
the log: `xcrun notarytool log <submission id> --keychain-profile taaad-notary --keychain …`. Always read it, even
after success, for warnings.

`./scripts/package.sh --no-notarize` builds and signs only, for a dry run. Never publish such a package.

## Each release: publish

1. Bump `CFBundleShortVersionString` and `CFBundleVersion` in `Resources/Info.plist` and `SignerInfo.version` in
   the Swift sources together, and add a CHANGELOG entry.
2. Attach `build/taaad-signer-<version>.pkg` to the matching GitHub release and put its SHA-256 in the release notes.
   This publishes to a public repo: confirm the repo, the tag and the file before uploading.
3. Afterwards, download the asset from the release page and check that its SHA-256 matches the one in the notes.

## Installing from a release (users)

```sh
# 1. Download taaad-signer-<version>.pkg from the GitHub release, then verify it before installing:
shasum -a 256 taaad-signer-<version>.pkg            # must equal the SHA-256 in the release notes
pkgutil --check-signature taaad-signer-<version>.pkg # "Developer ID Installer: Richard Myers (U26DY5A9DU)", trusted timestamp
spctl --assess --type install -vv taaad-signer-<version>.pkg   # "accepted", "source=Notarized Developer ID"

# 2. Install (or double-click the package):
sudo installer -pkg taaad-signer-<version>.pkg -target /

# 3. Check:
taaad doctor
```

Updates install the same way. Keys survive an update: they belong to the access group, not to a particular build.

**Uninstall:** first remove each App's key with `taaad apps remove <slug>` (it asks for Touch ID), or rotate the Apps
elsewhere. Then:

```sh
sudo rm -rf "/Library/Application Support/taaad/taaad-signer.app"
sudo pkgutil --forget org.de-otio.taaad-signer
```

Keys left behind stay in the data-protection keychain, where nothing else can read them. They become usable again
once the helper is reinstalled.
