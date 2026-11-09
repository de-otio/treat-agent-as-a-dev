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


# --- plan 0004: `read` is derived from the installation --------------------------------------

from hypothesis import given, strategies as st  # noqa: E402

WIDE = {**GRANTED, "security_events": "read", "actions": "write", "checks": "admin",
        "profile": "write"}


def test_read_derives_every_granted_permission_at_read(world, capsys):
    world.granted = dict(WIDE)
    assert mint_mod.run(_args()) == 0
    assert world.posts[0]["permissions"] == {
        "metadata": "read", "contents": "read", "pull_requests": "read", "issues": "read",
        "security_events": "read", "actions": "read", "checks": "read"}


def test_read_only_flag_narrows(world, capsys):
    world.granted = dict(WIDE)
    assert mint_mod.run(_args(only="metadata,contents")) == 0
    assert world.posts[0]["permissions"] == {"metadata": "read", "contents": "read"}


def test_read_only_flag_ungranted_key_refused_before_mint(world, capsys):
    rc = mint_mod.run(_args(only="metadata,security_events"))
    out = capsys.readouterr()
    assert rc == 1 and out.out == "" and world.posts == []
    assert "security_events" in out.err


def test_read_exclude_flag_drops_key(world, capsys):
    world.granted = dict(WIDE)
    assert mint_mod.run(_args(exclude="actions,not_granted")) == 0
    assert "actions" not in world.posts[0]["permissions"]
    assert world.posts[0]["permissions"]["security_events"] == "read"


@pytest.mark.parametrize("kind", ["write", "contents-write"])
@pytest.mark.parametrize("flag", ["only", "exclude"])
def test_narrowing_flags_refused_outside_read(world, capsys, kind, flag):
    rc = mint_mod.run(_args(permissions=kind, **{flag: "contents"}))
    out = capsys.readouterr()
    assert rc == 1 and out.out == "" and world.posts == []


@pytest.mark.parametrize("value", ["", "Contents", "contents,", "a-b", "x" * 65])
def test_bad_permission_list_refused(world, capsys, value):
    rc = mint_mod.run(_args(only=value))
    assert rc == 1 and capsys.readouterr().out == "" and world.posts == []


def test_only_and_exclude_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["mint", "--app", "a", "--repo", "o/r", "--permissions", "read", "--json",
             "--only", "contents", "--exclude", "issues"])


def test_derived_body_still_refuses_widened_response(world, capsys):
    world.granted = dict(WIDE)
    world.minted_perms = {"metadata": "read", "security_events": "write"}
    rc = mint_mod.run(_args())
    assert rc == 1 and capsys.readouterr().out == ""


def test_422_under_read_names_both_causes(world, capsys):
    world.post_resp = _Resp(422, text='{"message":"Invalid permission level"}')
    rc = mint_mod.run(_args())
    out = capsys.readouterr()
    assert rc == 1 and out.out == "" and TOKEN not in out.err
    assert "--exclude" in out.err and "selected repositories" in out.err
    assert out.err.splitlines()[-1] == "taaad: error=github status=422"


@pytest.mark.parametrize("kind", ["write", "contents-write"])
def test_422_under_other_classes_names_repo_cause_only(world, capsys, kind):
    world.post_resp = _Resp(422, text='{"message":"x"}')
    rc = mint_mod.run(_args(permissions=kind))
    out = capsys.readouterr()
    assert rc == 1 and out.out == ""
    assert "--exclude" not in out.err and "selected repositories" in out.err
    assert out.err.splitlines()[-1] == "taaad: error=github status=422"


def test_non_422_error_has_no_hint(world, capsys):
    world.post_resp = _Resp(403, text='{"message":"x"}')
    rc = mint_mod.run(_args())
    out = capsys.readouterr()
    assert rc == 1 and "selected repositories" not in out.err and "--exclude" not in out.err


_names = st.from_regex(r"[a-z][a-z_]{0,20}", fullmatch=True)
_granted = st.dictionaries(_names | st.sampled_from(["workflows", "profile"]), st.sampled_from(["read", "write", "admin"]),
                           min_size=1, max_size=12)


@given(_granted, st.data())
def test_read_body_properties(granted, data):
    keys = sorted(granted)
    mode = data.draw(st.sampled_from(["none", "only", "exclude"]))
    sub = frozenset(data.draw(st.lists(st.sampled_from(keys), unique=True)))
    only = sub if mode == "only" else None
    exclude = sub if mode == "exclude" else frozenset()
    expected = {k for k in granted if k not in mint_mod._NO_READ_LEVEL and k not in exclude
                and (only is None or k in only)}
    try:
        body = mint_mod.permission_body("read", granted, only=only, exclude=exclude)
    except ValueError:
        assert not expected  # the only refusal possible here: nothing left to request
        return
    assert set(body.values()) == {"read"}
    assert set(body) == expected  # equality: a fixed key list would fail this


# --- the fixed failure line (`taaad: error=<code>[ status=<n>]`) --------------------------------

def _last_err_line(err: str) -> str:
    return [ln for ln in err.splitlines() if ln.strip()][-1]


@pytest.mark.parametrize("code,http,want", [
    ("github", 422, "taaad: error=github status=422"),
    ("github", 0, "taaad: error=github"),
    ("keychain", 99, "taaad: error=keychain"),           # not an HTTP status
    ("Bad Code", 0, "taaad: error=internal"),             # outside the shape
    ("x\ntaaad: error=ok", 0, "taaad: error=internal"),  # no line injection
    (None, 0, "taaad: error=internal"),
])
def test_failure_line_shape(code, http, want):
    assert mint_mod.failure_line(code, http) == want


@given(st.text(), st.integers())
def test_failure_line_is_one_fixed_line(code, http):
    line = mint_mod.failure_line(code, http)
    assert "\n" not in line
    assert line.startswith("taaad: error=")
    assert line.split()[1] in (f"error={code}", "error=internal")


@pytest.mark.parametrize("setup,kw,want", [
    (lambda w: None, {"repo": "other-org/widgets"}, "taaad: error=owner-mismatch"),
    (lambda w: None, {"repo": "bad repo"}, "taaad: error=usage"),
    (lambda w: setattr(w, "granted", {"metadata": "read"}), {"permissions": "contents-write"},
     "taaad: error=not-granted"),
    (lambda w: setattr(w, "minted_repos", ["widgets", "secrets"]), {}, "taaad: error=widened"),
    (lambda w: setattr(w, "post_resp", _Resp(500, text="boom")), {},
     "taaad: error=github status=500"),
    (lambda w: setattr(w, "post_resp", _Resp(422, text="x")), {},
     "taaad: error=github status=422"),
])
def test_every_failure_ends_with_the_fixed_line(world, capsys, setup, kw, want):
    setup(world)
    rc = mint_mod.run(_args(**kw))
    out = capsys.readouterr()
    assert rc == 1 and out.out == ""
    assert _last_err_line(out.err) == want


def test_tty_refusal_ends_with_the_fixed_line(world, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdout", _FakeTTY())
    assert mint_mod.run(_args()) == 2
    assert _last_err_line(capsys.readouterr().err) == "taaad: error=tty"


def test_success_prints_no_failure_line(world, capsys):
    assert mint_mod.run(_args()) == 0
    assert "taaad: error=" not in capsys.readouterr().err
