"""`taaad mint` — mint an installation token narrowed to ONE repository
and one permission class, printed as a single JSON object.

Intended for a pinned caller that pipes our stdout. Refuses a TTY
(no --force), never logs the token, and writes every error to stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from taaad import config, github, secrets
from taaad.slug import validate as validate_slug

_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_READ_SET = ("metadata", "contents", "pull_requests", "issues")


def _err(msg: str) -> int:
    print(f"error: taaad mint: {msg}", file=sys.stderr)
    return 1


def parse_repo(value: str) -> tuple[str, str]:
    if not _REPO_RE.fullmatch(value):
        raise ValueError(f"invalid --repo {value!r}: must be <owner>/<repo>")
    owner, name = value.split("/", 1)
    if name in (".", ".."):
        raise ValueError(f"invalid --repo {value!r}: bad repository name")
    return owner, name


def permission_body(kind: str, granted: dict[str, str]) -> dict[str, str]:
    """The requested permissions, always a subset of `granted`.
    Raises ValueError when the class cannot be satisfied."""
    if kind == "read":
        body = {k: "read" for k in _READ_SET if k in granted}
    elif kind == "contents-write":
        if granted.get("contents") not in ("write", "admin"):
            raise ValueError("contents:write is not granted to the installation")
        body = {"contents": "write", "metadata": "read"}
    elif kind == "write":
        body = dict(granted)
    else:  # argparse choices make this unreachable
        raise ValueError(f"unknown permission class {kind!r}")
    if not body:
        raise ValueError("installation grants no permission for this class")
    return body


def run(args: argparse.Namespace) -> int:
    if sys.stdout.isatty():
        return _refuse_tty()
    if not getattr(args, "json", False):
        return _err("--json is required")
    try:
        slug = validate_slug(args.app)
        owner, repo = parse_repo(args.repo)
    except ValueError as e:
        return _err(str(e))

    token = ""
    try:
        cfg = config.read_app(slug)
        if cfg.install_id is None:
            return _err(f"app {slug} has no install_id yet")
        pem = secrets.get_pem(cfg.keychain_key)
        try:
            jwt_token = github.app_jwt(cfg.app_id, pem)
        finally:
            pem = None  # noqa: F841
        inst = github.get_installation(jwt_token, cfg.install_id)
        login = (inst.get("account") or {}).get("login")
        granted = inst.get("permissions") or {}
        if not isinstance(login, str) or not login:
            return _err("installation has no account login")
        if login.casefold() != owner.casefold():
            return _err(
                f"repository owner {owner!r} is not the installation account "
                f"{login!r}; refusing"
            )
        try:
            body = permission_body(args.permissions, granted)
        except ValueError as e:
            return _err(str(e))

        resp = github.create_scoped_token(jwt_token, cfg.install_id, [repo], body)
        token = resp.get("token") if isinstance(resp, dict) else ""
        names = [r.get("name") for r in resp.get("repositories") or []]
        if not isinstance(token, str) or not token:
            return _err("response carried no token")
        if len(names) != 1 or not isinstance(names[0], str) or (
            names[0].casefold() != repo.casefold()
        ):
            return _err("response repositories are not exactly the requested one; refusing")
        got = resp.get("permissions") or {}
        if not isinstance(got, dict) or set(got) - set(body):
            return _err("response permissions exceed the request; refusing")
        out = {
            "token": token,
            "expiresAt": resp.get("expires_at"),
            "installationAccount": login,
            "installationId": cfg.install_id,
            "appId": cfg.app_id,
            "repositories": names,
            "permissions": got,
        }
    except (FileNotFoundError, RuntimeError, KeyError, TypeError, AttributeError,
            ValueError) as e:
        # GitHubError is a RuntimeError; its text is already redacted.
        return _err(github.redact(str(e), extra=(token,)))

    print(json.dumps(out))
    return 0


def _refuse_tty() -> int:
    print(
        "error: `taaad mint` would print a token to a TTY; pipe its stdout "
        "(there is no --force).",
        file=sys.stderr,
    )
    return 2
