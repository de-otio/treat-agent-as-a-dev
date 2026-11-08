#!/bin/bash
# Build a signed, notarised, stapled installer package for taaad-signer (see signer/RELEASING.md).
#
# Input: the signed bundle from build.sh (signer/build/taaad-signer.app).
# Output: signer/build/taaad-signer-<version>.pkg and its SHA-256.
#
# Environment:
#   TAAAD_INSTALLER_IDENTITY  Developer ID Installer identity (SHA-1 or "Developer ID Installer: …"); required
#   TAAAD_SIGN_KEYCHAIN       keychain holding both identities and the notary profile (the release keychain); optional
#   TAAAD_NOTARY_PROFILE      notarytool keychain profile name (xcrun notarytool store-credentials); required unless --no-notarize
#
# Flags: --no-notarize   build and sign only (for a dry run; never publish such a package)
set -euo pipefail

cd "$(dirname "$0")/.."
NOTARIZE=1
[ "${1:-}" = "--no-notarize" ] && NOTARIZE=0

die() { echo "package.sh: $*" >&2; exit 1; }

APP=build/taaad-signer.app
ID=org.de-otio.taaad-signer
REQ='identifier "org.de-otio.taaad-signer" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] and certificate leaf[field.1.2.840.113635.100.6.1.13] and certificate leaf[subject.OU] = U26DY5A9DU'
DEST="/Library/Application Support/taaad"

[ -n "${TAAAD_INSTALLER_IDENTITY:-}" ] || die "TAAAD_INSTALLER_IDENTITY is not set"
[ "$NOTARIZE" = 0 ] || [ -n "${TAAAD_NOTARY_PROFILE:-}" ] || die "TAAAD_NOTARY_PROFILE is not set (or pass --no-notarize)"
[ -d "$APP" ] || die "$APP not found; run scripts/build.sh first"

# 1. The bundle must be the signed, notarisable build: strict signature, our requirement, hardened runtime, timestamp.
# -R="<req>" (a leading "=" is codesign's own "inline requirement" marker; do not add another).
codesign --verify --strict --verbose=2 -R="$REQ" "$APP" || die "bundle signature does not verify against the taaad requirement"
info="$(codesign -dvv "$APP" 2>&1)"
grep -q 'flags=0x10000(runtime)' <<<"$info" || die "bundle is not signed with the hardened runtime"
grep -q '^Timestamp=' <<<"$info" || die "bundle has no secure timestamp (notarisation would fail)"
[ -z "$(find "$APP" ! -type f ! -type d)" ] || die "bundle contains symlinks or special files"

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
OUT="build/taaad-signer-$VERSION.pkg"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 2. Payload root: exactly the bundle at its install location.
mkdir -p "$WORK/root$DEST"
cp -R "$APP" "$WORK/root$DEST/"
# Strip extended attributes: cp copies the provisioning profile's quarantine flag and its download URL (which names the
# team and profile ID), and pkgbuild would pack them into the shipped package as ._ files. They are not part of the
# code signature, so stripping keeps it valid (checked just below).
xattr -cr "$WORK/root"
[ -z "$(xattr -lr "$WORK/root")" ] || die "extended attributes remain in the payload"
codesign --verify --strict -R="$REQ" "$WORK/root$DEST/taaad-signer.app" || die "staged bundle no longer verifies"

# 3. Component plist: the bundle must NOT be relocatable, or Installer could "update" a copy found elsewhere on disk.
#    `pkgbuild --analyze` may not write the key at all, so set it if present and add it otherwise.
pkgbuild --analyze --root "$WORK/root" "$WORK/component.plist" >/dev/null
/usr/libexec/PlistBuddy -c 'Set :0:BundleIsRelocatable false' "$WORK/component.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c 'Add :0:BundleIsRelocatable bool false' "$WORK/component.plist"
/usr/libexec/PlistBuddy -c 'Set :0:BundleOverwriteAction upgrade' "$WORK/component.plist"

# 4. Component package (no scripts: nothing runs as root during install), owned root:wheel.
pkgbuild --root "$WORK/root" --component-plist "$WORK/component.plist" \
  --identifier "$ID" --version "$VERSION" --install-location / --ownership recommended \
  "$WORK/taaad-signer-component.pkg"

# Assert what the docs promise: not relocatable, no scripts, no AppleDouble entries, nothing outside the bundle.
pkgutil --expand "$WORK/taaad-signer-component.pkg" "$WORK/expanded"
grep -q 'relocatable="false"' "$WORK/expanded/PackageInfo" || die "package is relocatable"
[ ! -e "$WORK/expanded/Scripts" ] || die "package contains install scripts"
files="$(pkgutil --payload-files "$WORK/taaad-signer-component.pkg")"
if grep -q '/\._' <<<"$files"; then die "payload contains AppleDouble (._) entries"; fi
while IFS= read -r f; do
  case "$f" in
    .|./Library|"./Library/Application Support"|"./Library/Application Support/taaad"|"./Library/Application Support/taaad/taaad-signer.app"|"./Library/Application Support/taaad/taaad-signer.app/"*) ;;
    *) die "payload contains a path outside the bundle: $f" ;;
  esac
done <<<"$files"

# 5. Signed product archive (flat package) with the Developer ID Installer identity and a secure timestamp.
productbuild --package "$WORK/taaad-signer-component.pkg" \
  --sign "$TAAAD_INSTALLER_IDENTITY" ${TAAAD_SIGN_KEYCHAIN:+--keychain "$TAAAD_SIGN_KEYCHAIN"} --timestamp \
  "$OUT"

sig="$(pkgutil --check-signature "$OUT")"
grep -q 'Signed with a trusted timestamp' <<<"$sig" || die "package signature has no trusted timestamp"
grep -q '1\. Developer ID Installer: .*(U26DY5A9DU)' <<<"$sig" || die "package is not signed by the taaad Developer ID Installer identity"

if [ "$NOTARIZE" = 1 ]; then
  # 6. Notarise the outermost container (the .pkg), wait, and fail unless Accepted.
  result="$(xcrun notarytool submit "$OUT" --keychain-profile "$TAAAD_NOTARY_PROFILE" \
            ${TAAAD_SIGN_KEYCHAIN:+--keychain "$TAAAD_SIGN_KEYCHAIN"} --wait 2>&1)" || true
  echo "$result"
  grep -q 'status: Accepted' <<<"$result" || die "notarisation was not accepted; fetch the log with: xcrun notarytool log <id> --keychain-profile $TAAAD_NOTARY_PROFILE"

  # 7. Staple the ticket so Gatekeeper can check it offline, then assess like a user's Mac would.
  xcrun stapler staple "$OUT"
  xcrun stapler validate "$OUT"
  spctl --assess --type install --verbose=2 "$OUT" || die "Gatekeeper rejects the package"
fi

shasum -a 256 "$OUT"
echo "Package ready: $OUT"
