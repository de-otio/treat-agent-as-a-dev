"""Typed subprocess boundary to `taaad-signer` (signer/SPEC.md, sections 3-5, 8).

The helper is the only holder of each App's private key on macOS. This
module runs it with argv only, a fixed environment and (for `import`
alone) the PEM on stdin, then turns its one-line JSON into values or
typed exceptions.

Rules this module enforces:

- Exception messages never contain helper stdout (it can hold a token).
- A helper error is an error. Nothing here, or in `keystore`, retries a
  failed call through the keyring path.
- `mint` repeats the helper's widening check on its output.
"""

from __future__ import annotations

import json
import os
import re
import stat as statmod
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from taaad import acl
from taaad.slug import validate as validate_slug

SIGNER_APP = Path("/Library/Application Support/taaad/taaad-signer.app")
SIGNER_PATH = SIGNER_APP / "Contents" / "MacOS" / "taaad-signer"
CODESIGN = "/usr/bin/codesign"

# The helper gets no environment beyond this (it ignores it anyway).
_ENV = {"PATH": "/usr/bin:/bin"}

# A prompting command may wait up to 120 s for the helper's budget lock (Adapters.swift) and then wait for the person.
# This must stay above the lock wait, or a held lock kills the helper before it can report "rate-limited".
PROMPT_TIMEOUT_S = 240
QUICK_TIMEOUT_S = 30
_PROMPTING = frozenset({"import", "mint", "delete"})

_MAX_MESSAGE = 500

_LEVELS = {"read": 0, "write": 1, "admin": 2}
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_PERM_NAME_RE = re.compile(r"^[a-z][a-z_]{0,63}$")


# --- path check -----------------------------------------------------------

_LIBRARY = Path("/Library")


def path_problem(path: Path, lstat: Callable[[str], os.stat_result] = os.lstat) -> str | None:
    """Why `path` may not be run as the helper, or None. Pure over `lstat`.

    The executable must be a regular file (not a symlink: `lstat` never
    follows links), owned by uid 0, not writable by group or other. Every
    parent up to and including /Library must be a real directory owned by
    uid 0 and not writable by group or other.
    """
    def unsafe(p: Path, want_dir: bool) -> str | None:
        try:
            st = lstat(str(p))
        except OSError as e:
            return f"cannot stat {p}: {type(e).__name__}"
        if want_dir and not statmod.S_ISDIR(st.st_mode):
            return f"{p} is not a plain directory"
        if not want_dir and not statmod.S_ISREG(st.st_mode):
            return f"{p} is not a regular file"
        if st.st_uid != 0:
            return f"{p} is not owned by root"
        if st.st_mode & (statmod.S_IWGRP | statmod.S_IWOTH):
            return f"{p} is writable by group or other"
        return None

    problem = unsafe(path, want_dir=False)
    if problem:
        return problem
    for parent in path.parents:
        problem = unsafe(parent, want_dir=True)
        if problem:
            return problem
        if parent == _LIBRARY:
            break
    return None


# Injectable: tests cannot create root-owned files.
PATH_CHECK: Callable[[Path], str | None] = path_problem


# --- errors ---------------------------------------------------------------


class SignerError(Exception):
    """Base class. `code` is the helper's `error` string (or a local one)."""

    code = "internal"


class SignerMissing(SignerError):
    code = "missing"


class SignerProtocolError(SignerError):
    """Timeout, or output that is not the one JSON line the spec promises."""

    code = "protocol"


class KeyNotFound(SignerError):
    code = "not-found"


class PresenceDenied(SignerError):
    code = "presence-denied"


class RateLimited(SignerError):
    code = "rate-limited"

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GitHubError(SignerError):
    code = "github"

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class WidenedToken(SignerError):
    code = "widened"


class KeychainError(SignerError):
    code = "keychain"


class KeyExists(SignerError):
    code = "exists"


class BadKey(SignerError):
    code = "bad-key"


class AppMismatch(SignerError):
    code = "app-mismatch"


_SIMPLE: dict[int, type[SignerError]] = {
    3: KeyNotFound,
    4: PresenceDenied,
    7: WidenedToken,
    8: KeychainError,
    9: KeyExists,
    10: BadKey,
    11: AppMismatch,
}


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def exception_for(exit_code: int, stderr: str) -> SignerError:
    """Pure: the typed exception for a failed helper run.

    Only the helper's stderr is consulted (`{"error":…,"message":…}`);
    stdout never reaches an exception.
    """
    message = ""
    extra: Mapping[str, object] = {}
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            extra = parsed
            m = parsed.get("message")
            if isinstance(m, str):
                message = m[:_MAX_MESSAGE]
    label = message or f"taaad-signer exited {exit_code}"
    if exit_code == 5:
        return RateLimited(label, _as_int(extra.get("retry_after")))
    if exit_code == 6:
        return GitHubError(label, _as_int(extra.get("status")))
    cls = _SIMPLE.get(exit_code)
    if cls is not None:
        return cls(label)
    return SignerError(label)


# --- widening check -------------------------------------------------------


def widening_violation(
    requested_perms: Mapping[str, str],
    requested_repos: Iterable[str],
    perms: Mapping[str, str],
    repository_selection: str,
    repos: Iterable[str],
) -> str | None:
    """Pure: None when the token is no wider than requested, else why.

    With permissions requested, every returned permission must be a
    requested name at a level no higher than requested. With
    repositories requested, the selection must be `selected` and every
    returned repository (case-insensitively) must have been requested.
    Nothing requested means nothing to compare. `metadata: read` is allowed
    even unrequested; `metadata` at `write` or above is not.
    """
    if requested_perms:
        for name, level in perms.items():
            want = requested_perms.get(name)
            if want is None and name == "metadata" and level == "read":
                continue  # GitHub always adds metadata:read (SPEC section 9, item 10)
            if want is None:
                return f"token carries unrequested permission {name!r}"
            if level not in _LEVELS or want not in _LEVELS or _LEVELS[level] > _LEVELS[want]:
                return f"token permission {name!r} is above the requested level"
    wanted = {r.lower() for r in requested_repos}
    if wanted:
        if repository_selection != "selected":
            return "token is not limited to selected repositories"
        for r in repos:
            if r.lower() not in wanted:
                return f"token reaches unrequested repository {r!r}"
    return None


# --- results --------------------------------------------------------------


@dataclass(frozen=True)
class MintedToken:
    token: str = field(repr=False)  # never in a repr or traceback
    expires_at: str
    permissions: Mapping[str, str]
    repository_selection: str
    repositories: tuple[str, ...]
    cached: bool
    account: str


@dataclass(frozen=True)
class KeyStatus:
    slug: str
    present: bool
    app_id: int | None


@dataclass(frozen=True)
class ImportResult:
    slug: str
    app_id: int
    app_slug: str
    replaced: bool


# --- process boundary -----------------------------------------------------


def available() -> bool:
    return sys.platform == "darwin" and SIGNER_PATH.is_file()


def _run(args: list[str], *, stdin: str | None = None) -> bytes:
    """Run the helper; return stdout bytes of a successful (exit 0) run."""
    if not available():
        raise SignerMissing("taaad-signer is not installed")
    problem = PATH_CHECK(SIGNER_PATH)
    if problem is not None:
        raise SignerMissing(f"refusing to run taaad-signer: {problem}")
    timeout = PROMPT_TIMEOUT_S if args[0] in _PROMPTING else QUICK_TIMEOUT_S
    io: dict[str, object] = (
        {"input": stdin.encode()} if stdin is not None else {"stdin": subprocess.DEVNULL}
    )
    try:
        proc = subprocess.run(  # noqa: S603 - fixed absolute path, argv only
            [str(SIGNER_PATH), *args],
            capture_output=True,
            check=False,
            timeout=timeout,
            env=dict(_ENV),
            **io,  # type: ignore[arg-type]
        )
    except subprocess.TimeoutExpired as e:
        raise SignerProtocolError(f"taaad-signer {args[0]} timed out after {timeout}s") from e
    except OSError as e:
        raise SignerMissing(f"could not run taaad-signer: {type(e).__name__}") from e
    if proc.returncode != 0:
        raise exception_for(proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc.stdout


def _json_line(out: bytes) -> object:
    lines = [ln for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
    if len(lines) != 1:
        raise SignerProtocolError("taaad-signer output was not a single JSON line")
    try:
        return json.loads(lines[0])
    except ValueError:
        # Deliberately no excerpt: stdout can contain a token.
        raise SignerProtocolError("taaad-signer output was not valid JSON") from None


def _object(out: bytes) -> dict:
    v = _json_line(out)
    if not isinstance(v, dict):
        raise SignerProtocolError("taaad-signer output was not a JSON object")
    return v


def _str(d: Mapping[str, object], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str):
        raise SignerProtocolError(f"taaad-signer output lacks string field {key!r}")
    return v


def _int(d: Mapping[str, object], key: str) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        raise SignerProtocolError(f"taaad-signer output lacks integer field {key!r}")
    return v


def _check_perms(perms: Mapping[str, str]) -> None:
    for name, level in perms.items():
        if not _PERM_NAME_RE.fullmatch(name) or level not in _LEVELS:
            raise ValueError(f"invalid permission {name!r}={level!r}")


def _check_repos(repos: Iterable[str]) -> None:
    for r in repos:
        # A leading "-" is refused here (stricter than the helper) so a name
        # can never be read as an option.
        if not _REPO_RE.fullmatch(r) or r.startswith("-"):
            raise ValueError(f"invalid repository name {r!r}")


def _check_id(n: int, what: str) -> None:
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 2**53:
        raise ValueError(f"invalid {what}: {n!r}")


# --- commands -------------------------------------------------------------


def status(slug: str) -> KeyStatus:
    validate_slug(slug)
    d = _object(_run(["status", slug]))
    present = d.get("present")
    if not isinstance(present, bool):
        raise SignerProtocolError("taaad-signer status lacks boolean 'present'")
    app_id = d.get("app_id")
    return KeyStatus(slug, present, app_id if isinstance(app_id, int) else None)


def has_key(slug: str) -> bool:
    return status(slug).present


def import_key(slug: str, app_id: int, pem: str, replace: bool) -> ImportResult:
    validate_slug(slug)
    _check_id(app_id, "app_id")
    args = ["import", slug, str(app_id)] + (["--replace"] if replace else [])
    d = _object(_run(args, stdin=pem))
    return ImportResult(slug, _int(d, "app_id"), _str(d, "app_slug"), bool(d.get("replaced")))


def mint(
    slug: str,
    install_id: int,
    repos: Iterable[str] = (),
    perms: Mapping[str, str] | None = None,
) -> MintedToken:
    validate_slug(slug)
    _check_id(install_id, "install_id")
    repo_list = tuple(repos)
    perm_map = dict(perms or {})
    _check_repos(repo_list)
    _check_perms(perm_map)

    args = ["mint", slug, str(install_id)]
    for r in repo_list:
        args += ["--repo", r]
    for name in sorted(perm_map):
        args += ["--perm", f"{name}={perm_map[name]}"]

    d = _object(_run(args))
    got_perms = d.get("permissions")
    got_repos = d.get("repositories")
    if not isinstance(got_perms, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in got_perms.items()
    ):
        raise SignerProtocolError("taaad-signer mint output has malformed 'permissions'")
    if not isinstance(got_repos, list) or not all(isinstance(r, str) for r in got_repos):
        raise SignerProtocolError("taaad-signer mint output has malformed 'repositories'")
    token = MintedToken(
        token=_str(d, "token"),
        expires_at=_str(d, "expires_at"),
        permissions=MappingProxyType(dict(got_perms)),
        repository_selection=_str(d, "repository_selection"),
        repositories=tuple(got_repos),
        cached=bool(d.get("cached")),
        account=_str(d, "account"),
    )
    reason = widening_violation(
        perm_map, repo_list, token.permissions, token.repository_selection, token.repositories
    )
    if reason is not None:
        raise WidenedToken(f"refused a token wider than requested: {reason}")
    return token


def app_meta(slug: str) -> dict:
    validate_slug(slug)
    return _object(_run(["app-meta", slug]))


def installations(slug: str) -> list[dict]:
    validate_slug(slug)
    v = _json_line(_run(["installations", slug]))
    if not isinstance(v, list) or not all(isinstance(i, dict) for i in v):
        raise SignerProtocolError("taaad-signer installations output was not a list of objects")
    return v


def installation(slug: str, install_id: int) -> dict:
    """GET /app/installations/<id> via the helper (no prompt): holds
    `account.login` and the granted `permissions`."""
    validate_slug(slug)
    _check_id(install_id, "install_id")
    return _object(_run(["installation", slug, str(install_id)]))


def delete(slug: str) -> None:
    validate_slug(slug)
    _object(_run(["delete", slug]))


def verify_installed() -> str | None:
    """None when the installed bundle satisfies the designated requirement,
    otherwise the reason. Never raises."""
    if sys.platform != "darwin":
        return "taaad-signer is macOS only"
    if not SIGNER_PATH.is_file():
        return f"taaad-signer is not installed at {SIGNER_APP}"
    try:
        proc = subprocess.run(  # noqa: S603
            [CODESIGN, "--verify", "--strict", f"-R={acl.SIGNER_CSREQ}", str(SIGNER_APP)],
            capture_output=True,
            check=False,
            timeout=QUICK_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            env=dict(_ENV),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"could not run codesign: {type(e).__name__}"
    if proc.returncode == 0:
        return None
    detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    tail = f": {detail[-1][:_MAX_MESSAGE]}" if detail else ""
    return f"codesign --verify failed (exit {proc.returncode}){tail}"
