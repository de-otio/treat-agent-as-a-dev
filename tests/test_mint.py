"""`taaad mint` — narrowed installation tokens. HTTP is mocked at
`github.requests`; no network, no real JWT signing, no keychain."""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import dataclass, field

import pytest

from taaad import cli, config, github, secrets
from taaad.commands import mint as mint_mod

TOKEN = "ghs_SECRETTOKEN1234567890abcdef"


@dataclass
class _Resp:
    status_code: int = 200
    payload: dict = field(default_factory=dict)
    text: str = ""

    def json(self):
        return self.payload


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


GRANTED = {
    "metadata": "read",
    "contents": "write",
    "pull_requests": "write",
    "issues": "write",
    "workflows": "write",
}


class World:
    def __init__(self):
        self.login = "Example-Org"
        self.granted = dict(GRANTED)
        self.get_resp = None
        self.post_resp = None
        self.posts: list[dict] = []
        self.minted_repos = None  # override response repositories
        self.minted_perms = None


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(
        config,
        "read_app",
        lambda slug: config.AppConfig(1, slug, 777, 4242, "Example-Org",
                                      f"github-app-{slug}-pem", "t"),
    )
    monkeypatch.setattr(secrets, "get_pem", lambda _k: "PEM")
    monkeypatch.setattr(github, "app_jwt", lambda _a, _p: "fake.jwt.token")

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/app/installations/4242")
        return w.get_resp or _Resp(200, {"account": {"login": w.login},
                                         "permissions": w.granted})

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/app/installations/4242/access_tokens")
        w.posts.append(json)
        if w.post_resp is not None:
            return w.post_resp
        repos = w.minted_repos if w.minted_repos is not None else json["repositories"]
        perms = w.minted_perms if w.minted_perms is not None else json["permissions"]
        return _Resp(201, {"token": TOKEN, "expires_at": "2026-01-01T00:00:00Z",
                           "repositories": [{"name": n} for n in repos],
                           "permissions": perms})

    monkeypatch.setattr(github.requests, "get", fake_get)
    monkeypatch.setattr(github.requests, "post", fake_post)
    return w


def _args(**kw):
    d = dict(app="example-bot", repo="example-org/widgets",
             permissions="read", json=True)
    d.update(kw)
    return argparse.Namespace(**d)


def test_owner_mismatch_refused(world, capsys):
    rc = mint_mod.run(_args(repo="other-org/widgets"))
    out = capsys.readouterr()
    assert rc != 0 and out.out == "" and world.posts == []
    assert "not the installation account" in out.err


def test_owner_match_case_insensitive(world, capsys):
    assert mint_mod.run(_args(repo="EXAMPLE-ORG/widgets")) == 0


def test_read_class_intersects_granted(world, capsys):
    world.granted = {"metadata": "read", "contents": "write", "workflows": "write"}
    assert mint_mod.run(_args(permissions="read")) == 0
    assert world.posts == [{"repositories": ["widgets"],
                            "permissions": {"metadata": "read", "contents": "read"}}]


def test_read_class_full(world, capsys):
    assert mint_mod.run(_args(permissions="read")) == 0
    assert world.posts[0]["permissions"] == {
        "metadata": "read", "contents": "read",
        "pull_requests": "read", "issues": "read"}


def test_contents_write_body(world, capsys):
    assert mint_mod.run(_args(permissions="contents-write")) == 0
    assert world.posts[0]["permissions"] == {"contents": "write", "metadata": "read"}


def test_contents_write_refused_when_not_granted(world, capsys):
    world.granted = {"metadata": "read", "contents": "read"}
    rc = mint_mod.run(_args(permissions="contents-write"))
    out = capsys.readouterr()
    assert rc != 0 and out.out == "" and world.posts == []
    assert "contents:write" in out.err


def test_write_is_granted_unchanged(world, capsys):
    assert mint_mod.run(_args(permissions="write")) == 0
    assert world.posts[0]["permissions"] == GRANTED


def test_output_shape(world, capsys):
    assert mint_mod.run(_args()) == 0
    obj = json.loads(capsys.readouterr().out)
    assert set(obj) == {"token", "expiresAt", "installationAccount",
                        "installationId", "appId", "repositories", "permissions"}
    assert obj["token"] == TOKEN and obj["repositories"] == ["widgets"]
    assert obj["installationId"] == 4242 and obj["appId"] == 777
    assert obj["installationAccount"] == "Example-Org"


def test_tty_refused(world, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdout", _FakeTTY())
    rc = mint_mod.run(_args())
    assert rc == 2 and world.posts == []
    assert TOKEN not in capsys.readouterr().err


def test_no_force_flag():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["mint", "--app", "a", "--repo", "o/r", "--permissions", "read",
             "--json", "--force"])


def test_json_flag_required():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["mint", "--app", "a", "--repo", "o/r", "--permissions", "read"])


def test_extra_repositories_refused(world, capsys):
    world.minted_repos = ["widgets", "secrets"]
    rc = mint_mod.run(_args())
    out = capsys.readouterr()
    assert rc != 0 and out.out == "" and TOKEN not in out.err


def test_wrong_repository_refused(world, capsys):
    world.minted_repos = ["other"]
    rc = mint_mod.run(_args())
    out = capsys.readouterr()
    assert rc != 0 and out.out == ""


def test_widened_response_permissions_refused(world, capsys):
    world.minted_perms = {"metadata": "read", "administration": "write"}
    rc = mint_mod.run(_args())
    assert rc != 0 and capsys.readouterr().out == ""


def test_raised_response_permission_level_refused(world, capsys):
    # Same key as requested, higher level: read was asked, write came back.
    world.minted_perms = {"metadata": "read", "contents": "write"}
    rc = mint_mod.run(_args())
    assert rc != 0 and capsys.readouterr().out == ""


def test_undocumented_response_permission_level_refused(world, capsys):
    world.minted_perms = {"metadata": "read", "contents": "owner"}
    rc = mint_mod.run(_args())
    assert rc != 0 and capsys.readouterr().out == ""


def test_token_never_in_stderr_on_http_error(world, capsys):
    world.post_resp = _Resp(
        422, text=f'{{"message":"bad","echo":"{TOKEN}","h":"Bearer abc.def.ghi12345"}}')
    rc = mint_mod.run(_args())
    out = capsys.readouterr()
    assert rc != 0 and out.out == ""
    assert TOKEN not in out.err and "422" in out.err
    assert "Bearer abc" not in out.err


def test_get_installation_error_redacted(world, capsys):
    world.get_resp = _Resp(401, text=f"nope {TOKEN}")
    rc = mint_mod.run(_args())
    out = capsys.readouterr()
    assert rc != 0 and TOKEN not in out.err and out.out == ""


def test_exception_text_cannot_leak_token(world, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError(f"boom {TOKEN}")
    monkeypatch.setattr(github, "create_scoped_token", boom)
    rc = mint_mod.run(_args())
    assert rc != 0 and TOKEN not in capsys.readouterr().err


@pytest.mark.parametrize("repo", [
    "-evil/repo", "example-org/..", "example-org/.", "example-org", "/repo",
    "example-org/", "example-org/re po", "example-org/a/b", "example-org/repo\n",
    "-example-org/widgets", "example-org/widgets;rm",
])
def test_invalid_repo_refused(world, repo, capsys):
    rc = mint_mod.run(_args(repo=repo))
    out = capsys.readouterr()
    assert rc != 0 and out.out == "" and world.posts == []


@pytest.mark.parametrize("slug", ["-bad", "Bad_Slug", "", "a/b"])
def test_invalid_slug_refused(world, slug, capsys):
    rc = mint_mod.run(_args(app=slug))
    assert rc != 0 and world.posts == []
