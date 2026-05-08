"""GitHub App API client. Mints App JWTs and installation tokens.

The PEM never leaves this module's caller frame; we accept it as a
str argument and discard locally after `jwt.encode`.
"""

from __future__ import annotations

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


def app_jwt_unknown_id(pem: str) -> str:
    """Self-discovery JWT — the App ID is the issuer claim. We don't
    have it, so we mint with a placeholder and fix this in
    `discover_app`. Used only when we *have* the PEM but not the ID
    yet (re-enrolment from existing keychain entries)."""
    raise NotImplementedError(
        "GitHub requires `iss` to be the numeric App ID; "
        "discover by other means"
    )


def get_app_by_slug(slug: str, pem: str, app_id_hint: int) -> AppMeta:
    """Fetch /apps/<slug> using a JWT — confirms slug↔id and returns
    canonical metadata. The caller passes the suspected `app_id` so
    we can mint the JWT; the response confirms it.
    """
    jwt_token = app_jwt(app_id_hint, pem)
    r = requests.get(
        f"{API}/apps/{slug}",
        headers={**HEADERS, "Authorization": f"Bearer {jwt_token}"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return AppMeta(id=int(data["id"]), slug=data["slug"])


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
