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

from taaad import config, github, keystore, signer
from taaad.slug import validate as validate_slug

_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
# `read` requests every permission the installation grants, each at `read`, except these: they have
# no `read` level. Source: the `app-permissions` schema in GitHub's REST OpenAPI description
# (github/rest-api-description, checked 2026-11-09), where these two are the only keys whose enum
# lacks "read". A permission GitHub adds later without a read level fails loudly (a 422 whose hint
# names `--exclude`); it never widens a token.
_NO_READ_LEVEL = frozenset({"workflows", "profile"})
# Permission names as the helper validates them (signer Narrowing.swift, Validate.permission).
_PERM_NAME_RE = re.compile(r"^[a-z][a-z_]{0,63}$")
# Permission levels, for checking that a response grants no more than was asked: a key that was not
# requested (-1) or a level GitHub has not documented (99) both count as exceeding.
_LEVEL = {"read": 0, "write": 1, "admin": 2}


# Exit codes (SPEC section 9, item 14). Stdout is empty on every one but 0.
EXIT_TTY = 2
EXIT_RATE_LIMITED = 3
EXIT_DENIED = 4


# Failure codes for the `taaad: error=<code>` line (see failure_line). The taaad-signer codes
# (signer.SignerError.code) pass through verbatim; these are the ones `mint` adds itself.
FAIL_TTY = "tty"
FAIL_USAGE = "usage"
FAIL_CONFIG = "config"
FAIL_OWNER = "owner-mismatch"
FAIL_GRANT = "not-granted"
FAIL_RESPONSE = "bad-response"
FAIL_WIDENED = "widened"
FAIL_GITHUB = "github"
FAIL_INTERNAL = "internal"
_CODE_RE = re.compile(r"^[a-z][a-z-]{0,31}$")


def failure_line(code: str, http: int = 0) -> str:
    """Pure: the one fixed stderr line every failing exit ends with, for callers that must not
    show free text (repo-aegis). Only a code from a fixed vocabulary and an HTTP status; never
    names, IDs or messages. A code outside the expected shape becomes `internal`."""
    code = code if isinstance(code, str) and _CODE_RE.fullmatch(code) else FAIL_INTERNAL
    tail = f" status={http}" if isinstance(http, int) and 100 <= http <= 599 else ""
    return f"taaad: error={code}{tail}"


def _err(msg: str, code: int = 1, *, fail: str = FAIL_INTERNAL, http: int = 0) -> int:
    print(f"error: taaad mint: {msg}", file=sys.stderr)
    print(failure_line(fail, http), file=sys.stderr)
    return code


def exit_code_for(e: signer.SignerError) -> int:
    if isinstance(e, signer.RateLimited):
        return EXIT_RATE_LIMITED
    if isinstance(e, signer.PresenceDenied):
        return EXIT_DENIED
    return 1


def parse_repo(value: str) -> tuple[str, str]:
    if not _REPO_RE.fullmatch(value):
        raise ValueError(f"invalid --repo {value!r}: must be <owner>/<repo>")
    owner, name = value.split("/", 1)
    if name in (".", ".."):
        raise ValueError(f"invalid --repo {value!r}: bad repository name")
    return owner, name


def parse_perm_list(value: str) -> frozenset[str]:
    """`--only` / `--exclude` value: comma-separated permission names."""
    names = [n.strip() for n in value.split(",")]
    if not names or any(not _PERM_NAME_RE.fullmatch(n) for n in names):
        raise ValueError(f"invalid permission list {value!r}: expected name[,name...]")
    return frozenset(names)


def permission_body(
    kind: str,
    granted: dict[str, str],
    *,
    only: frozenset[str] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """The requested permissions, always a subset of `granted`.
    Raises ValueError when the class cannot be satisfied."""
    if (only is not None or exclude) and kind != "read":
        raise ValueError("--only and --exclude apply to --permissions read only")
    if only is not None and exclude:
        raise ValueError("--only and --exclude cannot be combined")
    if kind == "read":
        if only is not None and (missing := sorted(only - granted.keys())):
            raise ValueError(
                "--only names permissions the installation does not grant: " + ", ".join(missing)
            )
        body = {
            k: "read" for k in granted
            if k not in _NO_READ_LEVEL and k not in exclude and (only is None or k in only)
        }
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


# GitHub answers 422 both for a repository outside the installation's selected repositories and
# for a permission level it refuses, and the body does not reliably say which.
_HINT_422_REPO = "the repository may not be in the installation's selected repositories"
_HINT_422_READ = (f"{_HINT_422_REPO}, or a granted permission may have no read level "
                  "(retry with --exclude <permission>)")


def _hint_422(kind: str) -> str:
    return _HINT_422_READ if kind == "read" else _HINT_422_REPO


def _is_422(e: Exception) -> bool:
    if isinstance(e, signer.GitHubError):
        return e.status == 422
    return "HTTP 422" in str(e)


def _flags(args: argparse.Namespace) -> tuple[frozenset[str] | None, frozenset[str]]:
    only = getattr(args, "only", None)
    exclude = getattr(args, "exclude", None)
    return (
        parse_perm_list(only) if only is not None else None,
        parse_perm_list(exclude) if exclude is not None else frozenset(),
    )


def run(args: argparse.Namespace) -> int:
    if sys.stdout.isatty():
        return _refuse_tty()
    if not getattr(args, "json", False):
        return _err("--json is required", fail=FAIL_USAGE)
    try:
        slug = validate_slug(args.app)
        owner, repo = parse_repo(args.repo)
        only, exclude = _flags(args)
        if (only is not None or exclude) and args.permissions != "read":
            raise ValueError("--only and --exclude apply to --permissions read only")
    except ValueError as e:
        return _err(str(e), fail=FAIL_USAGE)

    token = ""
    try:
        cfg = config.read_app(slug)
        if cfg.install_id is None:
            return _err(f"app {slug} has no install_id yet", fail=FAIL_CONFIG)
        inst = keystore.installation(cfg)
        login = (inst.get("account") or {}).get("login")
        granted = inst.get("permissions") or {}
        if not isinstance(login, str) or not login:
            return _err("installation has no account login", fail=FAIL_RESPONSE)
        if login.casefold() != owner.casefold():
            return _err(
                f"repository owner {owner!r} is not the installation account "
                f"{login!r}; refusing",
                fail=FAIL_OWNER,
            )
        try:
            body = permission_body(args.permissions, granted, only=only, exclude=exclude)
        except ValueError as e:
            return _err(str(e), fail=FAIL_GRANT)

        try:
            minted = keystore.mint_scoped(cfg, repo, body)
        except (github.GitHubError, signer.GitHubError) as e:
            # 422: the repository is not in the installation, or (under `read`) a granted
            # permission has no read level (_NO_READ_LEVEL is missing it). Exit code stays 1.
            if _is_422(e):
                return _err(github.redact(f"{e} ({_hint_422(args.permissions)})", extra=(token,)),
                            fail=FAIL_GITHUB, http=e.status)
            raise
        token = minted.token
        names = list(minted.repositories)
        if not isinstance(token, str) or not token:
            return _err("response carried no token", fail=FAIL_RESPONSE)
        if len(names) != 1 or not isinstance(names[0], str) or (
            names[0].casefold() != repo.casefold()
        ):
            return _err("response repositories are not exactly the requested one; refusing",
                        fail=FAIL_WIDENED)
        got = minted.permissions or {}
        if not isinstance(got, dict) or any(
            _LEVEL.get(v, 99) > _LEVEL.get(body.get(k, ""), -1) for k, v in got.items()
        ):
            return _err("response permissions exceed the request; refusing", fail=FAIL_WIDENED)
        out = {
            "token": token,
            "expiresAt": minted.expires_at,
            "installationAccount": login,
            "installationId": cfg.install_id,
            "appId": cfg.app_id,
            "repositories": names,
            "permissions": got,
        }
    except signer.SignerError as e:
        return _err(github.redact(f"taaad-signer: {e}", extra=(token,)), exit_code_for(e),
                    fail=e.code, http=getattr(e, "status", 0))
    except (FileNotFoundError, RuntimeError, KeyError, TypeError, AttributeError,
            ValueError) as e:
        # GitHubError is a RuntimeError; its text is already redacted.
        if isinstance(e, github.GitHubError):
            return _err(github.redact(str(e), extra=(token,)), fail=FAIL_GITHUB, http=e.status)
        fail = FAIL_CONFIG if isinstance(e, FileNotFoundError) else FAIL_INTERNAL
        return _err(github.redact(str(e), extra=(token,)), fail=fail)

    print(json.dumps(out))
    return 0


def _refuse_tty() -> int:
    print(
        "error: `taaad mint` would print a token to a TTY; pipe its stdout "
        "(there is no --force).",
        file=sys.stderr,
    )
    print(failure_line(FAIL_TTY), file=sys.stderr)
    return EXIT_TTY
