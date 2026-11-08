#!/bin/bash
# Installs the signed bundle to /Library/Application Support/taaad/ (run with sudo).
#   sudo scripts/install.sh [path/to/taaad-signer.app]   (default: build/taaad-signer.app)
set -euo pipefail

ID="org.de-otio.taaad-signer"
DEST_DIR="/Library/Application Support/taaad"
DEST="$DEST_DIR/taaad-signer.app"
EXPECTED_DR='identifier "org.de-otio.taaad-signer" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ and certificate leaf[subject.OU] = U26DY5A9DU'
EXPECTED_ENT='{
  "com.apple.application-identifier" : "U26DY5A9DU.org.de-otio.taaad-signer",
  "com.apple.developer.team-identifier" : "U26DY5A9DU",
  "keychain-access-groups" : [
    "U26DY5A9DU.org.de-otio.taaad-signer"
  ]
}'

die() { echo "install.sh: $*" >&2; exit 1; }

verify() { # $1 = bundle
  local app="$1" dr tmp
  [ -d "$app" ] || { echo "no bundle at $app" >&2; return 1; }
  codesign --verify --strict --verbose=2 "$app" || return 1
  dr="$(codesign -d -r- "$app" 2>&1 | sed -n 's/^designated => //p')"
  [ "$dr" = "$EXPECTED_DR" ] || { echo "designated requirement differs from SPEC section 1: $dr" >&2; return 1; }
  tmp="$(mktemp -d)"
  codesign -d --entitlements :- "$app" > "$tmp/ent.plist" 2>/dev/null || { rm -rf "$tmp"; return 1; }
  [ "$(plutil -convert json -r -o - -- "$tmp/ent.plist")" = "$EXPECTED_ENT" ] || { rm -rf "$tmp"; echo "entitlements differ from SPEC section 1" >&2; return 1; }
  rm -rf "$tmp"
  [ -f "$app/Contents/embedded.provisionprofile" ] || { echo "bundle has no embedded.provisionprofile" >&2; return 1; }
}

[ "$(id -u)" = 0 ] || die "must run as root (sudo)"

SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/build/taaad-signer.app}"
verify "$SRC" || die "source bundle does not verify; refusing to install"

mkdir -p "$DEST_DIR"
chown root:wheel "$DEST_DIR"
chmod 755 "$DEST_DIR"

STAGE="$DEST_DIR/.taaad-signer.app.new"
rm -rf "$STAGE"
ditto "$SRC" "$STAGE"
chown -R root:wheel "$STAGE"
chmod -R u=rwX,go=rX "$STAGE"
rm -rf "$DEST"
mv "$STAGE" "$DEST"

if ! verify "$DEST"; then
  rm -rf "$DEST"
  die "installed copy does not verify; removed"
fi
echo "Installed and verified: $DEST/Contents/MacOS/taaad-signer"
