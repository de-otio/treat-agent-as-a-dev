"""Git-config and remote-URL helpers for `taaad init`/`uninit`.

All git operations go through subprocess with argv lists. Never
shell strings.
"""

from __future__ import annotations

import fnmatch
import functools
import glob as _glob
import os
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


_HTTPS_GH_RE = re.compile(r"^https://github\.com/(?P<path>.+)$")
# SCP-style: git@host:path. host has no colon/slash; path doesn't start with /.
_SCP_SSH_RE = re.compile(r"^git@(?P<host>[^:/\s]+):(?P<path>[^/].*)$")
# ssh:// form: ssh://git@host[:port]/path
_SSH_URL_RE = re.compile(
    r"^ssh://git@(?P<host>[^:/\s]+)(?::\d+)?/(?P<path>.+)$"
)


_SSH_USER_CONFIG = Path.home() / ".ssh" / "config"
_SSH_SYSTEM_CONFIG = Path("/etc/ssh/ssh_config")
_SSH_TOKEN_RE = re.compile(r"^\s*([A-Za-z]+)\s*[=\s]\s*(.+?)\s*$")


def _matches_host(patterns: list[str], host: str) -> bool:
    """Match `host` against an SSH `Host`/`Match host` pattern list.
    Patterns may be globs (`*`, `?`); a leading `!` negates. A block
    matches if at least one positive pattern matches and no
    negation pattern matches. Case-insensitive (per OpenSSH semantics).
    """
    host_lc = host.lower()
    matched = False
    for raw in patterns:
        if raw.startswith("!"):
            if fnmatch.fnmatch(host_lc, raw[1:].lower()):
                return False
        else:
            if fnmatch.fnmatch(host_lc, raw.lower()):
                matched = True
    return matched


def _expand_includes(value: str, base_dir: Path) -> list[Path]:
    """Expand an SSH `Include` value: env vars, `~`, globs.
    Relative paths resolve against `base_dir` (~/.ssh/ for user
    config, /etc/ssh/ for system config), per OpenSSH semantics.
    """
    expanded = os.path.expandvars(os.path.expanduser(value))
    if not os.path.isabs(expanded):
        expanded = str(base_dir / expanded)
    return [Path(p) for p in sorted(_glob.glob(expanded))]


def _ssh_config_lookup(
    path: Path, queried_host: str, seen: set[Path], base_dir: Path
) -> str | None:
    """Walk a single SSH config file. Return the first `HostName` in
    the first `Host` block whose pattern list matches `queried_host`,
    honouring OpenSSH's "first match wins" semantics. Recurses into
    `Include` directives.

    `Match` blocks are skipped entirely. The reason is security: the
    only reason taaad needs SSH config resolution at all is to
    rewrite aliases like `git@github.com-personal:…` to HTTPS, and
    aliases live under `Host`, not `Match`. By never entering a
    `Match` block we avoid evaluating `Match exec`, which would run
    arbitrary shell commands during resolution. If a user's config
    relies on `Match host` for an alias, they should move it under a
    plain `Host` block — `Match host` was never strictly needed for
    that pattern.
    """
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if resolved in seen:
        return None  # cycle in Include directives
    seen.add(resolved)
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
        return None

    in_match_block = False
    in_matching_host = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0]
        m = _SSH_TOKEN_RE.match(line)
        if not m:
            continue
        key = m.group(1).lower()
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if key == "host":
            in_match_block = False
            patterns = value.split()
            in_matching_host = _matches_host(patterns, queried_host)
        elif key == "match":
            in_match_block = True
            in_matching_host = False
        elif key == "include" and not in_match_block:
            for inc in _expand_includes(value, base_dir):
                result = _ssh_config_lookup(inc, queried_host, seen, base_dir)
                if result is not None:
                    return result
        elif key == "hostname" and in_matching_host and not in_match_block:
            return value
    return None


@functools.lru_cache(maxsize=128)
def _resolve_ssh_hostname(host: str) -> str:
    """Resolve `host` to a canonical hostname via local SSH config.

    Walks `~/.ssh/config` then `/etc/ssh/ssh_config`, matching `host`
    against `Host` glob patterns (case-insensitive, with negation
    support). Returns the first `HostName` directive in a matching
    block. If no config file exists, no block matches, or every
    matching block omits `HostName`, returns `host` unchanged.

    This deliberately does *not* shell out to `ssh -G`. `ssh -G`
    fully evaluates the SSH config, including `Match exec` directives
    that run arbitrary shell commands. Earlier versions of taaad used
    `ssh -G` and were therefore a novel trigger for any `Match exec`
    payload sitting in a developer's `~/.ssh/config`. The in-process
    parser used here is strictly hostname-pattern-based — see
    `_ssh_config_lookup` for the Match-skipping rationale.
    """
    for config_path in (_SSH_USER_CONFIG, _SSH_SYSTEM_CONFIG):
        result = _ssh_config_lookup(
            config_path, host, set(), config_path.parent
        )
        if result is not None:
            return result
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
