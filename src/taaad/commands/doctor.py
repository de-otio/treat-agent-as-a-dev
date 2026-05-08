from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from taaad import config, github, paths, secrets


def _ok(msg: str) -> None:
    print(f"  [ok]  {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}", file=sys.stderr)


def _err(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)


def _check_dirs() -> int:
    fails = 0
    cfg = config.config_dir()
    print(f"config dir: {cfg}")
    if not cfg.exists():
        _warn("config dir does not exist yet (run `taaad register` to create)")
        return 0
    if sys.platform != "win32":
        mode = stat.S_IMODE(cfg.stat().st_mode)
        if mode & 0o077:
            _err(f"mode {mode:o} (group/other readable) — should be 0700")
            fails += 1
        else:
            _ok(f"mode {mode:o}")
    return fails


def _check_keyring() -> int:
    print(f"keyring backend: {secrets.backend_info()}")
    try:
        secrets.assert_safe_backend()
        _ok("backend is safe")
        return 0
    except RuntimeError as e:
        _err(str(e))
        return 1


def _check_path() -> int:
    p = paths.taaad_executable()
    print(f"taaad path: {p}")
    try:
        paths.assert_path_safe(p)
        _ok("ownership and mode are safe")
        return 0
    except RuntimeError as e:
        _err(str(e))
        return 1


def _check_env() -> int:
    fails = 0
    leaks = [k for k in os.environ if k.startswith("GIT_TRACE") or k == "GIT_CURL_VERBOSE"]
    if leaks:
        _err(f"token-leak env vars set: {', '.join(leaks)} — unset these")
        fails += 1
    else:
        _ok("no GIT_TRACE* / GIT_CURL_VERBOSE in env")
    return fails


def _check_app(a: config.AppConfig) -> int:
    fails = 0
    print(f"\napp: {a.slug}")
    if not secrets.has_pem(a.keychain_key):
        _err(f"PEM missing at keychain key {a.keychain_key!r}")
        return 1
    _ok(f"PEM present at {a.keychain_key}")

    if a.install_id is None:
        _warn("install_id is unknown — run `taaad install`")
        return fails

    try:
        pem = secrets.get_pem(a.keychain_key)
        token = github.installation_token(a.app_id, a.install_id, pem)
        del pem
        if token and token.startswith("ghs_"):
            _ok("installation token mints successfully")
        else:
            _err(f"installation token has unexpected shape")
            fails += 1
        del token
    except Exception as e:  # noqa: BLE001
        _err(f"token mint failed: {e}")
        fails += 1
    return fails


def _check_repo_context() -> int:
    """If cwd is inside a git repo with bot.app set, sanity-check
    the local config — but never error if cwd isn't a repo."""
    from taaad import git as gitmod
    try:
        root = gitmod.repo_root(Path.cwd())
    except gitmod.GitError:
        return 0
    bot_app = gitmod.get_config("bot.app", cwd=root)
    if not bot_app:
        return 0
    fails = 0
    print(f"\nrepo: {root} (bot.app={bot_app})")

    helper = gitmod.get_config("credential.helper", cwd=root)
    if helper and "taaad credential-helper" in helper:
        _ok(f"credential.helper: {helper}")
    elif helper:
        _warn(f"credential.helper is {helper!r} — does not look like taaad")

    hp_origins = gitmod.show_origin("core.hooksPath", cwd=root)
    if len(hp_origins) > 1:
        _err(f"core.hooksPath set in multiple sources: {hp_origins}")
        fails += 1
    elif hp_origins:
        origin, value = hp_origins[0]
        if "config.local" in origin or origin.endswith(".git/config"):
            _ok(f"core.hooksPath = {value} (from local config)")
        else:
            _warn(f"core.hooksPath set in {origin}, not local config")
    return fails


def run(args: argparse.Namespace) -> int:
    fails = 0
    fails += _check_dirs()
    fails += _check_keyring()
    fails += _check_path()
    fails += _check_env()

    apps = config.list_apps()
    if args.app:
        apps = [a for a in apps if a.slug == args.app]
        if not apps:
            _err(f"no such app: {args.app}")
            return 1
    if not apps:
        _warn("no apps registered yet")
    for a in apps:
        fails += _check_app(a)

    fails += _check_repo_context()

    print()
    if fails:
        print(f"doctor: {fails} issue(s) found", file=sys.stderr)
        return 1
    print("doctor: all checks passed")
    return 0
