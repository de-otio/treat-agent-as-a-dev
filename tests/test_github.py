"""GitHub API client unit tests. Network is mocked via monkeypatching
`requests.get` / `requests.post` at the module level. We don't speak
to api.github.com from CI, and we don't sign real JWTs (pyjwt is a
trusted dep; testing its RSA path here adds nothing)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from taaad import github


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


@pytest.fixture
def fake_jwt(monkeypatch):
    """Skip real RS256 signing — `get_app_meta` only cares that a
    bearer is attached, not that GitHub would accept it."""
    monkeypatch.setattr(github, "app_jwt", lambda _aid, _pem: "fake.jwt.token")


@pytest.fixture
def intercept_get(monkeypatch):
    captured: list[tuple[str, dict]] = []
    fake = _FakeResponse(200, {})

    def fake_get(url, headers=None, timeout=None):
        captured.append((url, dict(headers or {})))
        return fake

    monkeypatch.setattr(github.requests, "get", fake_get)
    return captured, fake


def test_get_app_meta_calls_app_endpoint(fake_jwt, intercept_get):
    captured, response = intercept_get
    response.payload = {"slug": "demo", "id": 42, "public": False}
    meta = github.get_app_meta(42, "PEM")
    assert meta == {"slug": "demo", "id": 42, "public": False}
    assert len(captured) == 1
    url, headers = captured[0]
    assert url == "https://api.github.com/app"
    # Authenticates as the App via JWT — not via user token.
    assert headers.get("Authorization") == "Bearer fake.jwt.token"


def test_get_app_meta_surfaces_public_flag(fake_jwt, intercept_get):
    """The drift-detection use case: if `public: true` comes back,
    `taaad doctor` needs to see it."""
    _, response = intercept_get
    response.payload = {"slug": "leaked", "id": 99, "public": True}
    meta = github.get_app_meta(99, "PEM")
    assert meta["public"] is True


def test_get_app_meta_propagates_http_errors(fake_jwt, intercept_get):
    _, response = intercept_get
    response.status_code = 401
    with pytest.raises(RuntimeError, match="HTTP 401"):
        github.get_app_meta(99, "PEM")
