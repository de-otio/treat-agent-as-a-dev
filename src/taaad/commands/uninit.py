"""`taaad uninit` — reverse `taaad init` in the current repo.

Each key is unset only if its value still looks like ours, unless
`--force` is given. The keychain entry and apps/<slug>.toml are NOT
touched (other repos may still need them).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from taaad import config, git


def _email_looks_like_bot(value: str | None, slug: str) -> bool:
    if not value:
        return False
    suffix = f"+{slug}[bot]@users.noreply.github.com"
    return value.endswith(suffix)


def _name_looks_like_bot(value: str | None, slug: str) -> bool:
    return value == f"{slug}[bot]"


def _helper_looks_like_taaad(value: str | None) -> bool:
    if not value:
        return False
    return "taaad" in value and "credential-helper" in value


def _hooks_looks_like_ours(value: str | None) -> bool:
    if not value:
        return False
    expected = str(config.hooks_dir())
    return Path(value).resolve() == Path(expected).resolve()


def run(args: argparse.Namespace) -> int:
    try:
        root = git.repo_root(Path.cwd())
    except git.GitError as e:
        print(f"not in a git working tree: {e}", file=sys.stderr)
        return 1

    bot_app = git.get_config("bot.app", cwd=root)
    if not bot_app:
        print("no bot.app set in this repo; nothing to do.", file=sys.stderr)
        return 0

    decisions: list[tuple[str, str, str]] = []  # (key, action, why)

    def consider(key: str, ok: bool) -> None:
        v = git.get_config(key, cwd=root)
        if v is None:
            decisions.append((key, "skip", "not set"))
            return
        if ok or args.force:
            git.unset_config(key, cwd=root)
            decisions.append((key, "unset", v[:60]))
        else:
            decisions.append((key, "kept", f"{v!r} doesn't look like ours"))

    consider("user.name", _name_looks_like_bot(git.get_config("user.name", cwd=root), bot_app))
    consider(
        "user.email",
        _email_looks_like_bot(git.get_config("user.email", cwd=root), bot_app),
    )
    consider(
        "credential.helper",
        _helper_looks_like_taaad(git.get_config("credential.helper", cwd=root)),
    )
    consider(
        "core.hooksPath",
        _hooks_looks_like_ours(git.get_config("core.hooksPath", cwd=root)),
    )
    git.unset_config("bot.app", cwd=root)
    decisions.append(("bot.app", "unset", bot_app))

    print(f"uninit decisions for {root}:", file=sys.stderr)
    for k, action, why in decisions:
        print(f"  {action:<5}  {k:<24}  {why}", file=sys.stderr)
    return 0
