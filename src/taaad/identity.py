"""Resolve which app/slug the current invocation should use, and
mint a fresh installation token for it. Shared by agent, env, and
credential-helper.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from taaad import config, git, github, secrets


def resolve_slug(explicit: str | None = None) -> str:
    """Return the slug to use, in priority order:

    1. explicit (--app flag)
    2. `bot.app` git config of the current working dir, if in a repo
    3. global config.toml `default_app`
    """
    if explicit:
        return explicit
    try:
        root = git.repo_root(Path.cwd())
        slug = git.get_config("bot.app", cwd=root)
        if slug:
            return slug
    except git.GitError:
        pass
    g = config.load_global()
    default = g.get("default_app")
    if isinstance(default, str) and default:
        return default
    raise SystemExit(
        "no app to use: pass --app <slug>, run `taaad init` in this "
        "repo, or set default_app in your global config.toml."
    )


def mint_token(slug: str) -> tuple[str, config.AppConfig]:
    cfg = config.read_app(slug)
    if cfg.install_id is None:
        raise SystemExit(
            f"app {slug} has no install_id yet — run "
            f"`taaad install {slug} --account <owner>`."
        )
    pem = secrets.get_pem(cfg.keychain_key)
    try:
        token = github.installation_token(cfg.app_id, cfg.install_id, pem)
    finally:
        pem = None  # noqa: F841
    return token, cfg


def env_for(slug: str, token: str, cfg: config.AppConfig) -> dict[str, str]:
    return {
        "GH_TOKEN": token,
        "GIT_AUTHOR_NAME": f"{slug}[bot]",
        "GIT_AUTHOR_EMAIL": f"{cfg.app_id}+{slug}[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": f"{slug}[bot]",
        "GIT_COMMITTER_EMAIL": f"{cfg.app_id}+{slug}[bot]@users.noreply.github.com",
    }


def scrub_trace_env(env: dict[str, str]) -> None:
    """Drop GIT_TRACE* / GIT_CURL_VERBOSE so a sub-shell doesn't
    log the token to stderr or a file (plan 0001 §14)."""
    for k in list(env):
        if k.startswith("GIT_TRACE") or k == "GIT_CURL_VERBOSE":
            del env[k]
