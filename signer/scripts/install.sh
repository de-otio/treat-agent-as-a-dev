#!/bin/bash
# Installs the signed bundle (cp -R, never ditto; refuses symlinks and other special files) to /Library/Application Support/taaad/ (run with sudo).
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

# $1 = source bundle: only regular files and directories (no symlinks, devices, sockets, fifos).
check_regular_only() {
  local odd
  odd="$(find "$1" ! -type f ! -type d -print 2>/dev/null)" || return 1
  if [ -n "$odd" ]; then echo "bundle contains entries that are neither regular files nor directories:" >&2; echo "$odd" >&2; return 1; fi
}

# $1 = installed tree: only regular files and directories (no symlinks, fifos, sockets, devices);
# every entry root:wheel; no group or other write.
assert_tree() {
  local bad
  bad="$(find "$1" ! -type f ! -type d -print)"
  [ -z "$bad" ] || { echo "entries that are neither regular files nor directories in installed tree:" >&2; echo "$bad" >&2; return 1; }
  bad="$(find "$1" \( ! -user root -o ! -group wheel \) -print)"
  [ -z "$bad" ] || { echo "entries not owned by root:wheel:" >&2; echo "$bad" >&2; return 1; }
  bad="$(find "$1" \( -perm -020 -o -perm -002 \) -print)"
  [ -z "$bad" ] || { echo "entries writable by group or other:" >&2; echo "$bad" >&2; return 1; }
}

# Allow tests to source the check functions without installing.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi

[ "$(id -u)" = 0 ] || die "must run as root (sudo)"

SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/build/taaad-signer.app}"
check_regular_only "$SRC" || die "refusing to install: unexpected entry types in the source bundle"
verify "$SRC" || die "source bundle does not verify; refusing to install"

[ ! -L "$DEST_DIR" ] || die "$DEST_DIR is a symlink; refusing to install through it"
mkdir -p "$DEST_DIR"
[ -d "$DEST_DIR" ] && [ ! -L "$DEST_DIR" ] || die "$DEST_DIR is not a plain directory"
chown root:wheel "$DEST_DIR"
chmod 755 "$DEST_DIR"

STAGE="$DEST_DIR/.taaad-signer.app.new"
rm -rf "$STAGE"
cp -R "$SRC" "$STAGE"
xattr -cr "$STAGE" # drop the profile's quarantine flag and download URL; not part of the signature (verify below)
chown -R root:wheel "$STAGE"
chmod -R u=rwX,go=rX "$STAGE"
assert_tree "$STAGE" || { rm -rf "$STAGE"; die "staged tree failed the ownership/symlink check; nothing installed"; }
rm -rf "$DEST"
mv "$STAGE" "$DEST"

if ! assert_tree "$DEST" || ! verify "$DEST"; then
  rm -rf "$DEST"
  die "installed copy does not verify; removed"
fi
echo "Installed and verified: $DEST/Contents/MacOS/taaad-signer"
