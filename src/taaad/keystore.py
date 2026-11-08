"""The single routing point between the signer helper and the legacy keyring.

An App's key is served by `taaad-signer` when we are on macOS, the helper
is installed and it holds a key for the slug. Otherwise the legacy
`secrets`/`github` path is used.

A signer error never falls back to the legacy path: a denied prompt or a
rate limit must not turn into a silent read of a Python-readable key.
`signer.has_key` itself failing is also an error, not a "no".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Literal

from taaad import config, github, secrets, signer

DESCRIBE_SIGNER = "taaad-signer"
DESCRIBE_LEGACY = "keychain (readable by Python)"
DESCRIBE_MISSING = "missing"


def signer_installed() -> bool:
    """macOS with the helper present: new keys go to the helper."""
    return sys.platform == "darwin" and signer.available()


def uses_signer(slug: str) -> bool:
    """True when the helper holds this slug's key."""
    return signer_installed() and signer.has_key(slug)


def mint(cfg: config.AppConfig) -> str:
    if cfg.install_id is None:
        raise ValueError(f"app {cfg.slug} has no install_id")
    if uses_signer(cfg.slug):
        return signer.mint(cfg.slug, cfg.install_id).token
    pem = secrets.get_pem(cfg.keychain_key)
    try:
        return github.installation_token(cfg.app_id, cfg.install_id, pem)
    finally:
        pem = None  # noqa: F841


@dataclass(frozen=True)
class ScopedToken:
    """A narrowed token, from either store. Fields are as received and
    unchecked on the legacy path; the caller validates them."""

    token: str = field(repr=False)
    expires_at: Any
    permissions: Any
    repositories: tuple[Any, ...]


def installation(cfg: config.AppConfig) -> dict:
    """`GET /app/installations/<id>`: `account.login` and granted permissions."""
    if cfg.install_id is None:
        raise ValueError(f"app {cfg.slug} has no install_id")
    if uses_signer(cfg.slug):
        return signer.installation(cfg.slug, cfg.install_id)
    pem = secrets.get_pem(cfg.keychain_key)
    try:
        jwt_token = github.app_jwt(cfg.app_id, pem)
    finally:
        pem = None  # noqa: F841
    return github.get_installation(jwt_token, cfg.install_id)


def mint_scoped(cfg: config.AppConfig, repo: str, perms: dict[str, str]) -> ScopedToken:
    """Mint a token for one repository and the given permissions."""
    if cfg.install_id is None:
        raise ValueError(f"app {cfg.slug} has no install_id")
    if uses_signer(cfg.slug):
        t = signer.mint(cfg.slug, cfg.install_id, repos=[repo], perms=perms)
        return ScopedToken(t.token, t.expires_at, dict(t.permissions), t.repositories)
    pem = secrets.get_pem(cfg.keychain_key)
    try:
        jwt_token = github.app_jwt(cfg.app_id, pem)
    finally:
        pem = None  # noqa: F841
    resp = github.create_scoped_token(jwt_token, cfg.install_id, [repo], perms)
    if not isinstance(resp, dict):
        resp = {}
    token = resp.get("token")
    names = tuple(r.get("name") for r in resp.get("repositories") or [])
    return ScopedToken(
        token if isinstance(token, str) else "",
        resp.get("expires_at"),
        resp.get("permissions") or {},
        names,
    )


def app_meta(cfg: config.AppConfig) -> dict:
    if uses_signer(cfg.slug):
        return signer.app_meta(cfg.slug)
    pem = secrets.get_pem(cfg.keychain_key)
    try:
        return github.get_app_meta(cfg.app_id, pem)
    finally:
        pem = None  # noqa: F841


def installations(cfg: config.AppConfig) -> list[dict]:
    if uses_signer(cfg.slug):
        return signer.installations(cfg.slug)
    pem = secrets.get_pem(cfg.keychain_key)
    try:
        return github.list_installations(cfg.app_id, pem)
    finally:
        pem = None  # noqa: F841


def store_new(slug: str, app_id: int, pem: str, keychain_key: str) -> Literal["signer", "keyring"]:
    """Store a freshly issued PEM. Returns where it went so the caller can
    print the legacy notice. With the helper installed, a failure raises
    `signer.SignerError` and nothing is written to the keyring."""
    if signer_installed():
        signer.import_key(slug, app_id, pem, replace=False)
        return "signer"
    secrets.set_pem(keychain_key, pem)
    return "keyring"


def has_key_for(slug: str, keychain_key: str) -> bool:
    return uses_signer(slug) or secrets.has_pem(keychain_key)


def has_key(cfg: config.AppConfig) -> bool:
    return has_key_for(cfg.slug, cfg.keychain_key)


def describe(cfg: config.AppConfig) -> str:
    if uses_signer(cfg.slug):
        return DESCRIBE_SIGNER
    if secrets.has_pem(cfg.keychain_key):
        return DESCRIBE_LEGACY
    return DESCRIBE_MISSING


def delete(cfg: config.AppConfig) -> None:
    """Remove the key from both stores. If the helper's delete fails (for
    example the prompt is denied) it raises before the legacy item is touched."""
    if uses_signer(cfg.slug):
        signer.delete(cfg.slug)
    if secrets.has_pem(cfg.keychain_key):
        secrets.delete_pem(cfg.keychain_key)
