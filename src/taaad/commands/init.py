"""`taaad init` — wire the current repo to a registered app.

Writes `.git/config` only. Per-key value-match gating is in
`uninit`; here we overwrite freely (init replaces v0.4's
inline credential helper).
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from taaad import config, git, paths
from taaad.slug import validate as validate_slug


def _pick_slug(explicit: str | None, owner: str | None) -> str:
    if explicit:
        return validate_slug(explicit)
    apps = config.list_apps()
    if not apps:
        raise SystemExit("no apps registered — run `taaad register` first.")
    matched = [a for a in apps if a.account == owner] if owner else []
    print("registered apps:", file=sys.stderr)
    for i, a in enumerate(apps, 1):
        marker = "  ← default" if matched and a.slug == matched[0].slug else ""
        print(f"  {i}. {a.slug}  (account={a.account or '?'}){marker}", file=sys.stderr)
    default_idx = (
        next((i + 1 for i, a in enumerate(apps) if a.slug == matched[0].slug), None)
        if matched else None
    )
    prompt = (
        f"\nWhich app? [1-{len(apps)}]"
        + (f" (default {default_idx})" if default_idx else "")
        + ": "
    )
    try:
        s = input(prompt).strip()
    except EOFError:
        s = ""
    if not s and default_idx:
        return apps[default_idx - 1].slug
    try:
        n = int(s)
        return apps[n - 1].slug
    except (ValueError, IndexError):
        raise SystemExit("no selection; aborting.")


def _credential_helper_value(slug: str) -> str:
    """Build the value for git's credential.helper.

    git invokes this as a shell command if it starts with `!`, or
    splits it on whitespace otherwise. We use the bare-command form
    (no `!`) so git argv-splits it: <abs-path-to-taaad>
    credential-helper <slug>.
    """
    exe = paths.taaad_executable()
    paths.assert_path_safe(exe)
    return f"{shlex.quote(exe)} credential-helper {slug}"


def run(args: argparse.Namespace) -> int:
    try:
        root = git.repo_root(Path.cwd())
    except git.GitError as e:
        print(f"not in a git working tree: {e}", file=sys.stderr)
        return 1

    origin_url = git.get_remote_url("origin", cwd=root)
    if origin_url:
        coerced = git.coerce_https(origin_url)
        if coerced:
            print(f"rewriting origin {origin_url} → {coerced}", file=sys.stderr)
            git.set_remote_url("origin", coerced, cwd=root)
            origin_url = coerced

    owner = git.parse_owner(origin_url) if origin_url else None
    slug = _pick_slug(args.app, owner)
    cfg = config.read_app(slug)

    if cfg.install_id is None:
        print(
            f"warning: {slug} has no install_id; pushes will fail until "
            f"`taaad install {slug} --account <owner>` runs.",
            file=sys.stderr,
        )

    git.set_config("bot.app", slug, cwd=root)
    git.set_config("user.name", f"{slug}[bot]", cwd=root)
    git.set_config(
        "user.email",
        f"{cfg.app_id}+{slug}[bot]@users.noreply.github.com",
        cwd=root,
    )
    git.set_config("credential.helper", _credential_helper_value(slug), cwd=root)

    config.record_used_by(slug, root)

    print(f"\n✅ wired {root} to {slug}.")
    print(f"  user.name:          {slug}[bot]")
    print(f"  user.email:         {cfg.app_id}+{slug}[bot]@users.noreply.github.com")
    print(f"  credential.helper:  taaad credential-helper {slug}")
    print(f"  bot.app:            {slug}")
    print(f"\nOptional: `taaad hooks install` to add the pre-commit guard.")
    return 0
