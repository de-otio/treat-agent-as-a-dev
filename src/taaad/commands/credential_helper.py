"""`taaad credential-helper <slug> [get|store|erase]` — git-credential helper.

Git invokes this on every HTTPS push. Re-verifies our binary path
at every call (plan 0001 §12), mints a fresh installation token,
emits `username=x-access-token\\npassword=<token>\\n\\n`.

Reads (and discards) the stdin key=value block git sends — we don't
need it but consuming it keeps git happy.
"""

from __future__ import annotations

import argparse
import sys

from taaad import identity, paths


def run(args: argparse.Namespace) -> int:
    op = args.operation
    if op != "get":
        # `store` and `erase` are no-ops; we don't cache.
        try:
            sys.stdin.read()
        except Exception:  # noqa: BLE001
            pass
        return 0

    paths.assert_path_safe(paths.taaad_executable())

    try:
        sys.stdin.read()
    except Exception:  # noqa: BLE001
        pass

    token, _cfg = identity.mint_token(args.slug)

    sys.stdout.write("username=x-access-token\n")
    sys.stdout.write(f"password={token}\n")
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0
