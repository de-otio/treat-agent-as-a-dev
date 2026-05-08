"""`taaad uninstall [--purge]` — machine teardown.

Removes the config dir. With --purge, also deletes keychain
entries. Never deletes the binary itself (`pipx uninstall taaad`
does that).
"""

from __future__ import annotations

import argparse
import shutil
import sys

from taaad import config, secrets


def run(args: argparse.Namespace) -> int:
    apps = config.list_apps()

    if args.purge and not args.ack_github_cleanup and apps:
        print(
            "\n🛑 Apps still on github.com require manual deletion:",
            file=sys.stderr,
        )
        for a in apps:
            print(
                f"  - https://github.com/settings/apps/{a.slug}/advanced",
                file=sys.stderr,
            )
        print(
            "\nRe-run with --ack-github-cleanup once those are deleted "
            "(or skip --purge to keep keychain entries).",
            file=sys.stderr,
        )
        return 3

    if args.purge:
        for a in apps:
            secrets.delete_pem(a.keychain_key)
            print(f"deleted keychain entry: {a.keychain_key}", file=sys.stderr)

    cfg_dir = config.config_dir()
    if cfg_dir.exists():
        shutil.rmtree(cfg_dir)
        print(f"removed {cfg_dir}", file=sys.stderr)
    else:
        print(f"no config dir at {cfg_dir}", file=sys.stderr)

    print(
        "\nThe taaad binary is still installed. To remove it:\n"
        "  pipx uninstall taaad",
        file=sys.stderr,
    )
    return 0
