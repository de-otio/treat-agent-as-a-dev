"""`taaad env` — print export statements for shell `eval`."""

from __future__ import annotations

import argparse
import shlex
import sys

from taaad import identity


def run(args: argparse.Namespace) -> int:
    slug = identity.resolve_slug(args.app)
    token, cfg = identity.mint_token(slug)
    e = identity.env_for(slug, token, cfg)
    if sys.platform == "win32":
        # PowerShell-friendly form
        for k, v in e.items():
            print(f"$env:{k} = {shlex.quote(v)}")
    else:
        for k, v in e.items():
            print(f"export {k}={shlex.quote(v)}")
    return 0
