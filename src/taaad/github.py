"""GitHub App API client. Mints App JWTs and installation tokens.

The PEM never leaves this module's caller frame; we accept it as a
str argument and discard locally after `jwt.encode`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import jwt
import requests

API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "taaad/0.5",
}


@dataclass(frozen=True)
class AppMeta:
    id: int
    slug: str


def app_jwt(app_id: int, pem: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 9 * 60, "iss": str(app_id)},
        pem,
        algorithm="RS256",
    )


def list_installations(app_id: int, pem: str) -> list[dict]:
    jwt_token = app_jwt(app_id, pem)
    r = requests.get(
        f"{API}/app/installations",
        headers={**HEADERS, "Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def find_installation(app_id: int, pem: str, account: str) -> dict | None:
    for inst in list_installations(app_id, pem):
        if inst["account"]["login"] == account:
            return inst
    return None


def installation_token(app_id: int, install_id: int, pem: str) -> str:
    jwt_token = app_jwt(app_id, pem)
    r = requests.post(
        f"{API}/app/installations/{install_id}/access_tokens",
        headers={**HEADERS, "Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


def manifest_conversion(code: str) -> dict:
    """POST /app-manifests/{code}/conversions — returns the new App's
    metadata including `id`, `slug`, `pem`, `webhook_secret`.
    """
    r = requests.post(
        f"{API}/app-manifests/{code}/conversions",
        headers=HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def get_app_meta(app_id: int, pem: str) -> dict:
    """GET /app — returns metadata of the App the JWT is for,
    including `public`, `slug`, `name`, and the permissions/events
    the App was registered with.

    Unlike `GET /apps/<slug>` (which requires a user access token
    from the App's owner — historically broken for private Apps,
    see v0.5.2 changelog), `GET /app` authenticates as the App
    itself via JWT, so it works regardless of public/private state
    or whether the caller is the owner.

    Used by `taaad doctor` to detect drift on Operating Rule 8
    (Apps must stay private — `manifest.public` may have been
    toggled `True` after creation via the github.com Edit page).
    """
    jwt_token = app_jwt(app_id, pem)
    r = requests.get(
        f"{API}/app",
        headers={**HEADERS, "Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


class GitHubError(RuntimeError):
    """An HTTP failure with a message that is safe to print: status
    code plus a redacted, truncated body. Never carries a token."""


_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"),
    re.compile(r"-----BEGIN [A-Z ]+-----.*?(?:-----END [A-Z ]+-----|$)", re.S),
    re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._~+/=-]{8,}"),
]


def redact(text: str, *, extra: tuple[str, ...] = (), limit: int = 300) -> str:
    """Strip anything that looks like a credential from `text` (and any
    literal in `extra`), then truncate."""
    for s in extra:
        if s:
            text = text.replace(s, "<redacted>")
    for pat in _SECRET_PATTERNS:
        text = pat.sub("<redacted>", text)
    return text[:limit]


def _bearer(jwt_token: str) -> dict[str, str]:
    return {**HEADERS, "Authorization": f"Bearer {jwt_token}"}


def _fail(what: str, r) -> GitHubError:
    try:
        body = str(r.text)
    except Exception:  # noqa: BLE001
        body = ""
    return GitHubError(f"{what}: HTTP {r.status_code} {redact(body)}".rstrip())


def get_installation(jwt_token: str, install_id: int) -> dict:
    """GET /app/installations/{id} (app JWT) -> includes
    `account.login` and the granted `permissions`."""
    try:
        r = requests.get(
            f"{API}/app/installations/{install_id}",
            headers=_bearer(jwt_token),
            timeout=10,
        )
    except requests.RequestException as e:
        raise GitHubError(f"GET installation failed: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise _fail("GET installation failed", r)
    return r.json()


def create_scoped_token(
    jwt_token: str, install_id: int, repositories: list[str], permissions: dict[str, str]
) -> dict:
    """POST /app/installations/{id}/access_tokens with a narrowed body.
    Returns the raw response JSON (contains the token — callers must
    not log it)."""
    try:
        r = requests.post(
            f"{API}/app/installations/{install_id}/access_tokens",
            headers=_bearer(jwt_token),
            json={"repositories": repositories, "permissions": permissions},
            timeout=10,
        )
    except requests.RequestException as e:
        raise GitHubError(f"mint failed: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise _fail("mint failed", r)
    return r.json()
