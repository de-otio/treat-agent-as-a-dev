"""Source for the shared pre-commit hook script.

We ship the hook as a template and write it to <config>/hooks/
on `taaad hooks install`. The hook reads `bot.app` from the local
git config and refuses commits whose author email doesn't match
the expected `<id>+<slug>[bot]@users.noreply.github.com` shape.

Implemented as `#!/bin/sh` so Git for Windows runs it through its
bundled bash. No PowerShell variant.
"""

PRE_COMMIT = r"""#!/bin/sh
# taaad pre-commit hook
# Refuses commits not authored by the bot identity declared in
# `git config bot.app`.

slug="$(git config --local --get bot.app 2>/dev/null)"
if [ -z "$slug" ]; then
    exit 0
fi

actual="$(git config user.email)"
expected_suffix="+${slug}[bot]@users.noreply.github.com"

case "$actual" in
    *"$expected_suffix") exit 0 ;;
    *)
        echo "refusing: commit author is $actual" >&2
        echo "expected the ${slug}[bot] identity. Launch via" >&2
        echo "  taaad agent <cli> ..." >&2
        echo "or run \`. <(taaad env)\` to source the bot identity." >&2
        exit 1 ;;
esac
"""
