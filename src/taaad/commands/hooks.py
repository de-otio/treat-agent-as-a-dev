"""`taaad hooks install|uninstall` — manage `core.hooksPath`."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from taaad import config, git, hook_template


def _write_hook() -> Path:
    config.ensure_dirs()
    p = config.hooks_dir() / "pre-commit"
    p.write_text(hook_template.PRE_COMMIT)
    if sys.platform != "win32":
        os.chmod(p, 0o755)
    return p


def _install() -> int:
    try:
        root = git.repo_root(Path.cwd())
    except git.GitError as e:
        print(f"not in a git working tree: {e}", file=sys.stderr)
        return 1

    origins = git.show_origin("core.hooksPath", cwd=root)
    if origins:
        for origin, value in origins:
            print(f"core.hooksPath already set: {value} (in {origin})", file=sys.stderr)
        print(
            "refusing to clobber. Inspect the existing setup and "
            "either remove it or use a delegating wrapper.",
            file=sys.stderr,
        )
        return 1

    hook_path = _write_hook()
    git.set_config("core.hooksPath", str(config.hooks_dir()), cwd=root)
    print(f"✅ wrote {hook_path}")
    print(f"   set core.hooksPath = {config.hooks_dir()} in {root}/.git/config")
    return 0


def _uninstall() -> int:
    try:
        root = git.repo_root(Path.cwd())
    except git.GitError as e:
        print(f"not in a git working tree: {e}", file=sys.stderr)
        return 1
    value = git.get_config("core.hooksPath", cwd=root)
    if not value:
        print("core.hooksPath not set; nothing to do.", file=sys.stderr)
        return 0
    if Path(value).resolve() != config.hooks_dir().resolve():
        print(
            f"core.hooksPath = {value} is not taaad's; refusing to unset.",
            file=sys.stderr,
        )
        return 1
    git.unset_config("core.hooksPath", cwd=root)
    print("unset core.hooksPath")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.hooks_cmd == "install":
        return _install()
    if args.hooks_cmd == "uninstall":
        return _uninstall()
    print(f"unknown hooks subcommand {args.hooks_cmd}", file=sys.stderr)
    return 2
