"""Git-config and remote-URL helpers for `taaad init`/`uninit`.

All git operations go through subprocess with argv lists. Never
shell strings.
"""

from __future__ import annotations

import re
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


_SSH_GH_RE = re.compile(r"^git@github\.com:(.+?)(?:\.git)?$")
_HTTPS_GH_RE = re.compile(r"^https://github\.com/(.+?)(?:\.git)?$")


def parse_owner(url: str) -> str | None:
    """Return 'owner' from a github.com remote URL, else None."""
    for pat in (_HTTPS_GH_RE, _SSH_GH_RE):
        m = pat.match(url)
        if m:
            path = m.group(1)
            if "/" in path:
                return path.split("/", 1)[0]
    return None


_SSH_GH_FULL = re.compile(r"^git@github\.com:(.+)$")


def coerce_https(url: str) -> str | None:
    """Return an https:// version of a github.com SSH URL, or None
    if the URL is already https or doesn't match a known pattern."""
    if url.startswith("https://github.com/"):
        return None
    m = _SSH_GH_FULL.match(url)
    if m:
        return f"https://github.com/{m.group(1)}"
    return None
