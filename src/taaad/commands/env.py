"""`taaad env` — print export statements for shell `eval`."""

from __future__ import annotations

import argparse
import shlex
import sys

from taaad import identity


def run(args: argparse.Namespace) -> int:
    # Refuse to print `GH_TOKEN` to a terminal — it would land in
    # scrollback, terminal history, screen-share recordings, and
    # any logging tool watching the TTY. The intended usage is
    # `eval "$(taaad env)"` (or PowerShell equivalent) where stdout
    # is a pipe. `--force` is an explicit override for unusual
    # debugging.
    if sys.stdout.isatty() and not getattr(args, "force", False):
        print(
            "error: `taaad env` would print GH_TOKEN to a TTY (it would "
            "land in scrollback).\n"
            "       Use: eval \"$(taaad env)\"  (POSIX) "
            "or: Invoke-Expression (taaad env | Out-String) (PowerShell)\n"
            "       Pass --force if you really want to print to the terminal.",
            file=sys.stderr,
        )
        return 2

    slug = identity.resolve_slug(args.app)
    token, cfg = identity.mint_token(slug)
    e = identity.env_for(slug, token, cfg)

    print(
        "# taaad env: piping GH_TOKEN to stdout. Do not log this output.",
        file=sys.stderr,
    )
    if sys.platform == "win32":
        # PowerShell-friendly form
        for k, v in e.items():
            print(f"$env:{k} = {shlex.quote(v)}")
    else:
        for k, v in e.items():
            print(f"export {k}={shlex.quote(v)}")
    return 0
