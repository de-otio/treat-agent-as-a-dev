#!/bin/sh
# usage: . bin/agent-env.sh   (or `source bin/agent-env.sh` in bash)
#
# Substitute these placeholders during setup (RUNBOOK Step 5):
#   <APP_ID>     — numeric App ID (Step 1 output)
#   <INSTALL_ID> — numeric Installation ID (Step 3 output)
#   <APP_SLUG>   — bot slug as returned by GitHub (Step 1 output)

APP_ID="<APP_ID>"
INSTALL_ID="<INSTALL_ID>"
APP_SLUG="<APP_SLUG>"
KEYCHAIN_KEY="github-app-${APP_SLUG}-pem"

case "$(uname -s)" in
  Darwin)
    PEM="$(security find-generic-password -s "$KEYCHAIN_KEY" -w)"
    ;;
  Linux)
    if command -v secret-tool >/dev/null 2>&1; then
      PEM="$(secret-tool lookup service "$KEYCHAIN_KEY")"
    elif command -v pass >/dev/null 2>&1; then
      PEM="$(pass show "$KEYCHAIN_KEY")"
    else
      echo "no secret-tool or pass found" >&2
      return 1 2>/dev/null || exit 1
    fi
    ;;
  *)
    echo "unsupported OS: $(uname -s)" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

GH_TOKEN="$(printf %s "$PEM" | python3 "$(dirname -- "$0")/app-token.py" \
              "$APP_ID" "$INSTALL_ID")"
unset PEM

export GH_TOKEN
export GIT_AUTHOR_NAME="${APP_SLUG}[bot]"
export GIT_AUTHOR_EMAIL="${APP_ID}+${APP_SLUG}[bot]@users.noreply.github.com"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
