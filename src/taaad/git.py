"""Git-config and remote-URL helpers for `taaad init`/`uninit`.

All git operations go through subprocess with argv lists. Never
shell strings.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None) -> str:
    p = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def repo_root(cwd: Path | None = None) -> Path:
    out = _run(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(out.strip())


def get_config(key: str, cwd: Path | None = None, scope: str = "local") -> str | None:
    p = subprocess.run(
        ["git", "config", f"--{scope}", "--get", key],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        return p.stdout.rstrip("\n")
    return None


def set_config(key: str, value: str, cwd: Path | None = None) -> None:
    _run(["config", "--local", key, value], cwd=cwd)


def unset_config(key: str, cwd: Path | None = None) -> None:
    p = subprocess.run(
        ["git", "config", "--local", "--unset", key],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    # exit 5 = key not present; treat as success
    if p.returncode not in (0, 5):
        raise GitError(f"git config --unset {key} failed: {p.stderr.strip()}")


def show_origin(key: str, cwd: Path | None = None) -> list[tuple[str, str]]:
    """Return [(origin, value), ...] for every source that sets `key`."""
    p = subprocess.run(
        ["git", "config", "--show-origin", "--get-all", key],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return []
    out: list[tuple[str, str]] = []
    for line in p.stdout.splitlines():
        if "\t" in line:
            origin, value = line.split("\t", 1)
            out.append((origin, value))
    return out


def get_remote_url(remote: str = "origin", cwd: Path | None = None) -> str | None:
    p = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def set_remote_url(remote: str, url: str, cwd: Path | None = None) -> None:
    _run(["remote", "set-url", remote, url], cwd=cwd)


_HTTPS_GH_RE = re.compile(r"^https://github\.com/(?P<path>.+)$")
# SCP-style: git@host:path. host has no colon/slash; path doesn't start with /.
_SCP_SSH_RE = re.compile(r"^git@(?P<host>[^:/\s]+):(?P<path>[^/].*)$")
# ssh:// form: ssh://git@host[:port]/path
_SSH_URL_RE = re.compile(
    r"^ssh://git@(?P<host>[^:/\s]+)(?::\d+)?/(?P<path>.+)$"
)


@functools.lru_cache(maxsize=128)
def _resolve_ssh_hostname(host: str) -> str:
    """Resolve `host` via `ssh -G`, returning the canonical hostname.

    `ssh -G <host>` parses the same `~/.ssh/config` (including
    `Match`/`Include` directives, env-var expansion, etc.) that the
    real ssh client uses for connection establishment, and prints
    the resolved settings. We read the `hostname` line.

    Falls back to returning `host` unchanged when ssh is not on PATH,
    when ssh exits non-zero, when parsing fails, or when the
    subprocess times out — degrading gracefully to "no alias
    resolution" rather than failing the whole `taaad init`.
    """
    if shutil.which("ssh") is None:
        return host
    try:
        p = subprocess.run(
            ["ssh", "-G", host],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return host
    if p.returncode != 0:
        return host
    for line in p.stdout.splitlines():
        if line.startswith("hostname "):
            return line.split(maxsplit=1)[1].strip()
    return host


def _is_github_host(host: str) -> bool:
    """True if `host` is github.com or an SSH alias resolving to it."""
    if host.lower() == "github.com":
        return True
    return _resolve_ssh_hostname(host).lower() == "github.com"


def _normalize_github_path(url: str) -> str | None:
    """Return the `owner/repo` path-portion if `url` points at
    github.com (HTTPS, SCP-style SSH, ssh:// form, or via a host
    alias resolved through `~/.ssh/config`). Else None.
    """
    m = _HTTPS_GH_RE.match(url)
    if m:
        return m.group("path")
    for pat in (_SCP_SSH_RE, _SSH_URL_RE):
        m = pat.match(url)
        if m and _is_github_host(m.group("host")):
            return m.group("path")
    return None


def parse_owner(url: str) -> str | None:
    """Return 'owner' from a github.com remote URL, else None.
    Handles HTTPS, SCP-style SSH, ssh:// form, and SSH host aliases
    that resolve to github.com via `~/.ssh/config`.
    """
    path = _normalize_github_path(url)
    if path is None:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    if "/" not in path:
        return None
    return path.split("/", 1)[0]


def coerce_https(url: str) -> str | None:
    """Return an https://github.com/... URL for any github.com SSH
    URL — including custom host aliases (e.g. `git@github.com-personal:…`,
    `git@gh-work:…`) resolved via `ssh -G`. Returns None if the URL
    is already https or doesn't point at github.com.
    """
    if url.startswith("https://github.com/"):
        return None
    path = _normalize_github_path(url)
    if path is None:
        return None
    return f"https://github.com/{path}"
