#!/bin/bash
# Builds, assembles, signs and verifies taaad-signer.app (SPEC section 7).
#
#   TAAAD_SIGN_IDENTITY   codesign identity (SHA-1 or name); required unless --no-sign
#   TAAAD_SIGNER_PROFILE  path to the Developer ID provisioning profile (not committed); required unless --no-sign
#
#   scripts/build.sh            build, sign, verify -> build/taaad-signer.app
#   scripts/build.sh --no-sign  build and assemble an UNSIGNED bundle -> build/unsigned/taaad-signer.app
#
# Refuses to sign if any pre-sign check fails; deletes the bundle if any post-sign check fails.
set -euo pipefail

ID="org.de-otio.taaad-signer"
TEAM="U26DY5A9DU"
APP_ID="$TEAM.$ID"
EXPECTED_DR='identifier "org.de-otio.taaad-signer" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = U26DY5A9DU'
# Entitlements exactly as SPEC section 1, as sorted readable JSON (plutil -r). Independent of the .entitlements file.
EXPECTED_ENT='{
  "com.apple.application-identifier" : "U26DY5A9DU.org.de-otio.taaad-signer",
  "com.apple.developer.team-identifier" : "U26DY5A9DU",
  "keychain-access-groups" : [
    "U26DY5A9DU.org.de-otio.taaad-signer"
  ]
}'

SIGNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$SIGNER_DIR/build"

die() { echo "build.sh: $*" >&2; exit 1; }

# $1 = plist file; prints it as sorted, readable JSON
plist_json() { plutil -convert json -r -o - -- "$1"; }

# Entitlements file must equal the spec exactly.
check_entitlements_file() {
  local actual
  actual="$(plist_json "$1")" || die "cannot read entitlements file $1"
  [ "$actual" = "$EXPECTED_ENT" ] || die "entitlements file $1 does not equal SPEC section 1 exactly"
}

# $1 = decoded profile plist; App ID and team must match.
check_profile_plist() {
  local app team
  app="$(/usr/libexec/PlistBuddy -c 'Print :Entitlements:com.apple.application-identifier' "$1" 2>/dev/null)" || die "profile has no application-identifier"
  team="$(/usr/libexec/PlistBuddy -c 'Print :TeamIdentifier:0' "$1" 2>/dev/null)" || die "profile has no TeamIdentifier"
  [ "$app" = "$APP_ID" ] || die "profile App ID is '$app', expected '$APP_ID'"
  [ "$team" = "$TEAM" ] || die "profile team is '$team', expected '$TEAM'"
}

# $1 = signed bundle. Strict verify, designated requirement and entitlements all equal the spec.
verify_signed_bundle() {
  local app="$1" tmp dr
  codesign --verify --strict --verbose=2 "$app" || return 1
  dr="$(codesign -d -r- "$app" 2>&1 | sed -n 's/^designated => //p')"
  [ "$dr" = "$EXPECTED_DR" ] || { echo "designated requirement differs from SPEC section 1:" >&2; echo "  got:      $dr" >&2; echo "  expected: $EXPECTED_DR" >&2; return 1; }
  tmp="$(mktemp -d)"
  codesign -d --entitlements :- "$app" > "$tmp/ent.plist" 2>/dev/null || { rm -rf "$tmp"; return 1; }
  [ "$(plist_json "$tmp/ent.plist")" = "$EXPECTED_ENT" ] || { rm -rf "$tmp"; echo "signed entitlements differ from SPEC section 1" >&2; return 1; }
  rm -rf "$tmp"
}

# Every keychain call in the shell adapter must take its query from KC.query( (SPEC section 9, item 8).
check_keychain_adapter() {
  local f="${1:-$SIGNER_DIR/Sources/taaad-signer/Keychain.swift}" calls viaquery
  [ -f "$f" ] || die "adapter source not found: $f"
  calls="$(grep -cE 'SecItem(Add|Update|Delete|CopyMatching)\(' "$f" || true)"
  viaquery="$(grep -cE 'SecItem(Add|Update|Delete|CopyMatching)\(KC\.query\(' "$f" || true)"
  [ "$calls" -gt 0 ] || die "no SecItem calls found in $f (guard would be vacuous)"
  [ "$calls" = "$viaquery" ] || die "$f: $calls SecItem call(s) but only $viaquery take their query from KC.query("
}

# Allow tests to source the check functions without running a build.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi

SIGN=1
for a in "$@"; do
  case "$a" in
    --no-sign) SIGN=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "unknown argument: $a" ;;
  esac
done

if [ "$SIGN" = 1 ]; then
  [ -n "${TAAAD_SIGN_IDENTITY:-}" ] || die "TAAAD_SIGN_IDENTITY is not set (refusing to build a signed bundle). Use --no-sign for an unsigned build."
  [ -n "${TAAAD_SIGNER_PROFILE:-}" ] || die "TAAAD_SIGNER_PROFILE is not set (path to the Developer ID provisioning profile)."
  [ -f "$TAAAD_SIGNER_PROFILE" ] || die "TAAAD_SIGNER_PROFILE does not exist: $TAAAD_SIGNER_PROFILE"
fi

check_entitlements_file "$SIGNER_DIR/Resources/taaad-signer.entitlements"
check_keychain_adapter

PROFILE_PLIST=""
if [ "$SIGN" = 1 ]; then
  PROFILE_PLIST="$(mktemp)"
  security cms -D -i "$TAAAD_SIGNER_PROFILE" > "$PROFILE_PLIST" 2>/dev/null || die "cannot decode profile $TAAAD_SIGNER_PROFILE"
  check_profile_plist "$PROFILE_PLIST"
  rm -f "$PROFILE_PLIST"
fi

cd "$SIGNER_DIR"
ARCHS=(--arch arm64 --arch x86_64)
if ! swift build -c release "${ARCHS[@]}" >/dev/null 2>&1; then
  echo "build.sh: universal build unavailable here; building arm64 only" >&2
  ARCHS=(--arch arm64)
  swift build -c release "${ARCHS[@]}" || die "swift build failed"
fi
BIN="$(swift build -c release "${ARCHS[@]}" --show-bin-path)/taaad-signer"
[ -x "$BIN" ] || die "built executable not found: $BIN"

OUT="$BUILD_DIR/unsigned"
[ "$SIGN" = 1 ] && OUT="$BUILD_DIR/staging"
rm -rf "$OUT"
APP="$OUT/taaad-signer.app"
mkdir -p "$APP/Contents/MacOS"
cp "$SIGNER_DIR/Resources/Info.plist" "$APP/Contents/Info.plist"
cp "$BIN" "$APP/Contents/MacOS/taaad-signer"
chmod 755 "$APP/Contents/MacOS/taaad-signer"

if [ "$SIGN" = 0 ]; then
  echo "Unsigned bundle (NOT installable): $APP"
  exit 0
fi

cp "$TAAAD_SIGNER_PROFILE" "$APP/Contents/embedded.provisionprofile"
codesign --force --sign "$TAAAD_SIGN_IDENTITY" --identifier "$ID" --options runtime --timestamp \
  --entitlements "$SIGNER_DIR/Resources/taaad-signer.entitlements" "$APP"

if ! verify_signed_bundle "$APP"; then
  rm -rf "$OUT"
  die "post-sign verification failed; bundle deleted"
fi

rm -rf "$BUILD_DIR/taaad-signer.app"
mv "$APP" "$BUILD_DIR/taaad-signer.app"
rm -rf "$OUT"
echo "Signed and verified: $BUILD_DIR/taaad-signer.app"
