"""Config-dir layout, app registry I/O, and the used-by registry.

The config dir holds non-secret app metadata only; PEMs live in the
OS secret store via secrets.py.
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import platformdirs
import tomli_w

from taaad.slug import validate as validate_slug

SCHEMA_VERSION = 1
APP_NAME = "taaad"


@dataclass(frozen=True)
class AppConfig:
    schema_version: int
    slug: str
    app_id: int
    install_id: int | None
    account: str | None
    keychain_key: str
    created_at: str

    def to_toml(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "slug": self.slug,
            "app_id": self.app_id,
            "keychain_key": self.keychain_key,
            "created_at": self.created_at,
        }
        if self.install_id is not None:
            d["install_id"] = self.install_id
        if self.account is not None:
            d["account"] = self.account
        return d


def config_dir() -> Path:
    override = os.environ.get("TAAAD_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def apps_dir() -> Path:
    return config_dir() / "apps"


def hooks_dir() -> Path:
    return config_dir() / "hooks"


def app_path(slug: str) -> Path:
    return apps_dir() / f"{validate_slug(slug)}.toml"


def used_by_path(slug: str) -> Path:
    return config_dir() / f"used-by-{validate_slug(slug)}.txt"


def global_config_path() -> Path:
    return config_dir() / "config.toml"


def ensure_dirs() -> None:
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    apps_dir().mkdir(parents=True, exist_ok=True)
    hooks_dir().mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(cfg, 0o700)
        os.chmod(apps_dir(), 0o700)
        os.chmod(hooks_dir(), 0o700)


def write_app(cfg: AppConfig) -> Path:
    if cfg.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"refusing to write schema_version={cfg.schema_version}; "
            f"this build only knows {SCHEMA_VERSION}"
        )
    ensure_dirs()
    path = app_path(cfg.slug)
    payload = tomli_w.dumps(cfg.to_toml()).encode()
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600 if sys.platform != "win32" else 0o666,
    )
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    return path


def read_app(slug: str) -> AppConfig:
    path = app_path(slug)
    if not path.exists():
        raise FileNotFoundError(
            f"no app config for slug {slug!r} at {path}; "
            f"run `taaad register` or `taaad install`"
        )
    if sys.platform != "win32":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            print(
                f"warning: {path} has mode {mode:o} (group/other readable); "
                f"tightening to 0600",
                file=sys.stderr,
            )
            os.chmod(path, 0o600)
    with path.open("rb") as f:
        data = tomllib.load(f)
    sv = int(data.get("schema_version", 0))
    if sv > SCHEMA_VERSION:
        raise RuntimeError(
            f"{path} is schema_version={sv}; this taaad only knows "
            f"{SCHEMA_VERSION}. Upgrade taaad."
        )
    return AppConfig(
        schema_version=sv,
        slug=validate_slug(data["slug"]),
        app_id=int(data["app_id"]),
        install_id=int(data["install_id"]) if "install_id" in data else None,
        account=data.get("account"),
        keychain_key=data["keychain_key"],
        created_at=data["created_at"],
    )


def list_apps() -> list[AppConfig]:
    if not apps_dir().exists():
        return []
    out: list[AppConfig] = []
    for p in sorted(apps_dir().glob("*.toml")):
        try:
            out.append(read_app(p.stem))
        except Exception as e:  # noqa: BLE001
            print(f"skipping {p}: {e}", file=sys.stderr)
    return out


def remove_app(slug: str) -> None:
    p = app_path(slug)
    if p.exists():
        p.unlink()


def keychain_key_for(slug: str) -> str:
    return f"github-app-{validate_slug(slug)}-pem"


_DEFAULT_ALLOWLIST = ["claude", "codex", "aider", "gh", "git"]


def load_global() -> dict:
    p = global_config_path()
    if not p.exists():
        return {}
    with p.open("rb") as f:
        return tomllib.load(f)


def allowed_commands() -> list[str]:
    g = load_global()
    cmds = g.get("agent", {}).get("allowed_commands")
    if cmds is None:
        return list(_DEFAULT_ALLOWLIST)
    if not isinstance(cmds, list) or not all(isinstance(c, str) for c in cmds):
        raise ValueError(
            f"agent.allowed_commands in {global_config_path()} must be a list of strings"
        )
    return cmds


def record_used_by(slug: str, repo_root: Path) -> None:
    path = used_by_path(slug)
    line = str(repo_root.resolve()) + "\n"
    existing: set[str] = set()
    if path.exists():
        existing = {l.rstrip("\n") for l in path.read_text().splitlines()}
    if line.rstrip("\n") in existing:
        return
    ensure_dirs()
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600 if sys.platform != "win32" else 0o666,
    )
    with os.fdopen(fd, "wb") as f:
        f.write(line.encode())


def read_used_by(slug: str) -> list[Path]:
    path = used_by_path(slug)
    if not path.exists():
        return []
    return [Path(l) for l in path.read_text().splitlines() if l.strip()]
