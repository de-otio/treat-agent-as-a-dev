"""Resolve and validate the running `taaad` binary's absolute path.

Used by `init` (to write `credential.helper`) and at every
`credential-helper` invocation (to re-verify path integrity, plan
0001 §12).
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


def taaad_executable() -> str:
    """Absolute path to invoke when shelling out to `taaad`.

    Prefer the running script's own absolute path (sys.argv[0] when
    the user is running `taaad ...`), then the first `taaad` on
    PATH, then a `<python> -m taaad` fallback string suitable for
    git's credential.helper config.
    """
    arg0 = sys.argv[0] if sys.argv else ""
    if arg0:
        base = os.path.basename(arg0).lower()
        if base in ("taaad", "taaad.exe", "taaad-script.py"):
            return os.path.realpath(arg0)
    found = shutil.which("taaad")
    if found:
        return os.path.realpath(found)
    return f"{sys.executable} -m taaad"


def assert_path_safe(path: str) -> None:
    """Verify the resolved binary path is owned by us and not
    world-writable, and the same of its ancestor directories.

    Skipped on Windows (different ACL semantics).
    Skipped if the path is the `python -m taaad` fallback string.
    """
    if sys.platform == "win32":
        return
    if " " in path:  # `python -m taaad` form — no static path to check
        return
    p = Path(path)
    uid = os.getuid()
    while True:
        try:
            st = p.stat()
        except FileNotFoundError:
            raise RuntimeError(f"taaad path {p} does not exist")
        if st.st_uid not in (uid, 0):
            raise RuntimeError(
                f"taaad path component {p} is owned by uid={st.st_uid}, "
                f"not the current user (uid={uid}) or root"
            )
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o022:
            raise RuntimeError(
                f"taaad path component {p} is group/world writable "
                f"(mode {mode:o})"
            )
        if p.parent == p:
            return
        p = p.parent
