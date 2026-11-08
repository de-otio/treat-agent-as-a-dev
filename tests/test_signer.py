"""The Python side of taaad-signer: argv/env/stdin, exit-code mapping,
the widening check, and `verify_installed`. Uses a fake helper
(tests/fake_signer.py); never the real helper, keychain or GitHub."""

from __future__ import annotations

import stat
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from taaad import acl, signer
from tests.fake_signer import TOKEN, fake_signer, mint_output  # noqa: F401

# The helper is macOS-only; these tests run a POSIX fake helper and check POSIX ownership.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="taaad-signer is macOS-only; tests use a POSIX fake helper")

_KIND = "RSA " + "PRIVATE " + "KEY"  # built at runtime: no literal PEM armour in the source
PEM = f"-----BEGIN {_KIND}-----\nAAAA\n-----END {_KIND}-----\n"


# --- process boundary ------------------------------------------------------


def test_mint_runs_helper_with_argv_only_and_fixed_env(fake_signer, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "leak-me")
    monkeypatch.setenv("TAAAD_CONFIG_DIR", "/leak")
    fake_signer.set("mint", stdout=mint_output(
        permissions={"contents": "read"}, repository_selection="selected", repositories=["Repo-A"],
    ))
    t = signer.mint("my-app", 42, repos=["repo-a", "Repo-B"], perms={"contents": "read"})
    assert t.token == TOKEN
    (call,) = fake_signer.calls()
    assert call["argv"] == ["mint", "my-app", "42", "--repo", "repo-a", "--repo", "Repo-B",
                            "--perm", "contents=read"]
    # The interpreter and macOS add a locale variable of their own; nothing of ours leaks.
    env = call["env"]
    assert env["PATH"] == "/usr/bin:/bin"
    assert set(env) <= {"PATH", "LC_CTYPE", "__CF_USER_TEXT_ENCODING", "LANG"}
    assert call["stdin"] == ""


def test_import_sends_pem_on_stdin_only(fake_signer):
    fake_signer.set("import", stdout={"slug": "my-app", "app_id": 7, "app_slug": "my-app", "replaced": True})
    r = signer.import_key("my-app", 7, PEM, replace=True)
    assert (r.app_id, r.app_slug, r.replaced) == (7, "my-app", True)
    (call,) = fake_signer.calls()
    assert call["argv"] == ["import", "my-app", "7", "--replace"]
    assert call["stdin"].strip() == PEM.strip()
    assert "BEGIN" not in " ".join(call["argv"])


def test_non_import_commands_get_no_stdin(fake_signer):
    fake_signer.set("status", stdout={"slug": "my-app", "present": True, "app_id": 7})
    assert signer.has_key("my-app") is True
    assert fake_signer.calls()[0]["stdin"] == ""


def test_status_app_meta_installations_delete(fake_signer):
    fake_signer.set("status", stdout={"slug": "a", "present": False, "app_id": None})
    st_ = signer.status("a")
    assert (st_.present, st_.app_id) == (False, None)
    fake_signer.set("app-meta", stdout={"id": 7, "public": False})
    assert signer.app_meta("a") == {"id": 7, "public": False}
    fake_signer.set("installations", stdout=[{"id": 1, "account": {"login": "x"}}])
    assert signer.installations("a") == [{"id": 1, "account": {"login": "x"}}]
    fake_signer.set("delete", stdout={"slug": "a", "deleted": True})
    assert signer.delete("a") is None


def test_missing_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(signer, "SIGNER_PATH", tmp_path / "nope")
    assert not signer.available()
    with pytest.raises(signer.SignerMissing):
        signer.status("a")


def test_unavailable_off_macos(fake_signer, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert not signer.available()
    with pytest.raises(signer.SignerMissing):
        signer.status("a")
    assert fake_signer.calls() == []


def test_timeout_is_a_typed_error(fake_signer, monkeypatch):
    monkeypatch.setattr(signer, "QUICK_TIMEOUT_S", 0.5)
    fake_signer.set("status", stdout={"present": True}, sleep=3)
    with pytest.raises(signer.SignerProtocolError):
        signer.status("a")


def test_prompting_commands_get_the_long_timeout(fake_signer, monkeypatch):
    seen: dict[str, float] = {}
    real = signer.subprocess.run

    def spy(*a, **kw):
        seen[a[0][1]] = kw["timeout"]
        return real(*a, **kw)

    monkeypatch.setattr(signer.subprocess, "run", spy)
    fake_signer.set("status", stdout={"present": False, "app_id": None})
    fake_signer.set("mint", stdout=mint_output())
    signer.status("a")
    signer.mint("a", 1)
    assert seen == {"status": 30, "mint": signer.PROMPT_TIMEOUT_S}
    # The helper waits up to 120 s for its budget lock before it can prompt; Python must not kill it first
    # (security review L1), or a held lock shows up as a protocol error instead of "rate-limited".
    assert signer.PROMPT_TIMEOUT_S > 120


@pytest.mark.parametrize("bad", ["", "UPPER", "-x", "a" * 40, "a b", "a/b"])
def test_invalid_slug_never_reaches_helper(fake_signer, bad):
    with pytest.raises(ValueError):
        signer.status(bad)
    assert fake_signer.calls() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repos": ["bad/name"]},
        {"repos": ["--perm"]},
        {"perms": {"Contents": "read"}},
        {"perms": {"contents": "owner"}},
    ],
)
def test_invalid_mint_arguments_never_reach_helper(fake_signer, kwargs):
    with pytest.raises(ValueError):
        signer.mint("a", 1, **kwargs)
    assert fake_signer.calls() == []


@pytest.mark.parametrize("n", [0, -1, 2**53 + 1, True])
def test_invalid_install_id(fake_signer, n):
    with pytest.raises(ValueError):
        signer.mint("a", n)
    assert fake_signer.calls() == []


# --- exit codes -> exceptions ---------------------------------------------

CASES = [
    (3, "not-found", signer.KeyNotFound, {}),
    (4, "presence-denied", signer.PresenceDenied, {}),
    (5, "rate-limited", signer.RateLimited, {"retry_after": 77}),
    (6, "github", signer.GitHubError, {"status": 502}),
    (7, "widened", signer.WidenedToken, {}),
    (8, "keychain", signer.KeychainError, {"osstatus": -25300}),
    (9, "exists", signer.KeyExists, {}),
    (10, "bad-key", signer.BadKey, {}),
    (11, "app-mismatch", signer.AppMismatch, {}),
    (1, "internal", signer.SignerError, {}),
    (2, "usage", signer.SignerError, {}),
    (99, "mystery", signer.SignerError, {}),
]


@pytest.mark.parametrize("code,name,cls,extra", CASES)
def test_exit_code_maps_to_exception(fake_signer, code, name, cls, extra):
    # stdout holds a token on purpose: it must never reach the exception.
    fake_signer.set(
        "mint",
        exit=code,
        stdout={"token": TOKEN},
        stderr={"error": name, "message": f"helper says {name}", **extra},
    )
    with pytest.raises(cls) as ei:
        signer.mint("a", 1)
    e = ei.value
    assert type(e) is cls
    assert isinstance(e, signer.SignerError)
    assert TOKEN not in str(e) and TOKEN not in repr(e)
    assert f"helper says {name}" in str(e)
    if cls is signer.RateLimited:
        assert e.retry_after == 77
    if cls is signer.GitHubError:
        assert e.status == 502


@pytest.mark.parametrize("stderr", [None, "", "not json at all", "[1,2]", '{"error":"x"}'])
def test_unparseable_stderr_still_gives_the_right_type(fake_signer, stderr):
    fake_signer.set("mint", exit=4, stdout=f"junk {TOKEN}", stderr=stderr)
    with pytest.raises(signer.PresenceDenied) as ei:
        signer.mint("a", 1)
    assert TOKEN not in str(ei.value)


def test_rate_limited_with_garbage_retry_after_is_zero(fake_signer):
    fake_signer.set("mint", exit=5, stderr={"error": "rate-limited", "message": "m", "retry_after": "soon"})
    with pytest.raises(signer.RateLimited) as ei:
        signer.mint("a", 1)
    assert ei.value.retry_after == 0


@pytest.mark.parametrize("stdout", [f"{TOKEN} not json", "[1]", f"{{}}\n{{}}", "", f'{{"token":"{TOKEN}"}}'])
def test_bad_success_output_is_a_protocol_error_without_stdout(fake_signer, stdout):
    fake_signer.set("mint", stdout=stdout)
    with pytest.raises(signer.SignerProtocolError) as ei:
        signer.mint("a", 1)
    assert TOKEN not in str(ei.value) and TOKEN not in repr(ei.value)


def test_minted_token_repr_hides_token(fake_signer):
    fake_signer.set("mint", stdout=mint_output())
    t = signer.mint("a", 1)
    assert TOKEN not in repr(t)


# --- widening: Python repeats the check ------------------------------------


@pytest.mark.parametrize(
    "asked,out",
    [
        # unrequested permission
        ({"perms": {"contents": "read"}},
         mint_output(permissions={"contents": "read", "issues": "read"})),
        # higher level
        ({"perms": {"contents": "read"}}, mint_output(permissions={"contents": "write"})),
        # unknown level
        ({"perms": {"contents": "read"}}, mint_output(permissions={"contents": "root"})),
        # unrequested repository
        ({"repos": ["a"]},
         mint_output(repository_selection="selected", repositories=["a", "b"])),
        # selection widened to all
        ({"repos": ["a"]}, mint_output(repository_selection="all", repositories=[])),
    ],
)
def test_widened_helper_output_is_refused(fake_signer, asked, out):
    fake_signer.set("mint", stdout=out)  # the fake "helper" exits 0 with a wide token
    with pytest.raises(signer.WidenedToken) as ei:
        signer.mint("a", 1, **asked)
    assert TOKEN not in str(ei.value)


def test_narrower_or_equal_output_is_accepted(fake_signer):
    fake_signer.set("mint", stdout=mint_output(
        permissions={"contents": "read"}, repository_selection="selected", repositories=["A"]))
    t = signer.mint("a", 1, repos=["a", "b"], perms={"contents": "write", "issues": "read"})
    assert dict(t.permissions) == {"contents": "read"}


def test_nothing_requested_accepts_anything(fake_signer):
    fake_signer.set("mint", stdout=mint_output(permissions={"contents": "admin"}))
    assert signer.mint("a", 1).token == TOKEN


names = st.from_regex(r"[a-z][a-z_]{0,8}", fullmatch=True)
levels = st.sampled_from(["read", "write", "admin"])
repo_names = st.from_regex(r"[A-Za-z0-9._-]{1,8}", fullmatch=True)


@st.composite
def requests_and_narrower_responses(draw):
    req = draw(st.dictionaries(names, levels, min_size=1, max_size=5))
    rank = {"read": 0, "write": 1, "admin": 2}
    resp = {}
    for n, lvl in req.items():
        if draw(st.booleans()):
            resp[n] = draw(st.sampled_from([x for x in rank if rank[x] <= rank[lvl]]))
    repos = draw(st.lists(repo_names, min_size=1, max_size=5))
    got = draw(st.lists(st.sampled_from(repos), max_size=5))
    got = [g.swapcase() if draw(st.booleans()) else g for g in got]
    return req, repos, resp, got


@given(requests_and_narrower_responses())
def test_widening_accepts_every_narrowing(data):
    req, repos, resp, got = data
    assert signer.widening_violation(req, repos, resp, "selected", got) is None


@given(requests_and_narrower_responses(), names, levels, repo_names)
def test_widening_rejects_every_widening(data, extra_name, extra_level, extra_repo):
    req, repos, resp, got = data
    rank = {"read": 0, "write": 1, "admin": 2}
    # unrequested permission name (metadata:read is implicitly allowed, item 10)
    if extra_name not in req and not (extra_name == "metadata" and extra_level == "read"):
        assert signer.widening_violation(req, repos, {**resp, extra_name: extra_level}, "selected", got)
    # raised level (only possible below admin)
    for n, lvl in req.items():
        if lvl != "admin":
            higher = [x for x in rank if rank[x] > rank[lvl]][0]
            assert signer.widening_violation(req, repos, {**resp, n: higher}, "selected", got)
    # unrequested repository
    if extra_repo.lower() not in {r.lower() for r in repos}:
        assert signer.widening_violation(req, repos, resp, "selected", [*got, extra_repo])
    # selection widened
    assert signer.widening_violation(req, repos, resp, "all", got)


# --- verify_installed ------------------------------------------------------


def _fake_codesign(tmp_path, monkeypatch, code: int, stderr: str = ""):
    log = tmp_path / "codesign-argv.txt"
    script = tmp_path / "codesign"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nopen({str(log)!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
        f"sys.stderr.write({stderr!r})\nsys.exit({code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(signer, "CODESIGN", str(script))
    return log


def test_verify_installed_ok_and_argv(fake_signer, tmp_path, monkeypatch):
    log = _fake_codesign(tmp_path, monkeypatch, 0)
    assert signer.verify_installed() is None
    assert log.read_text().split("\n") == [
        "--verify", "--strict", f"-R={acl.SIGNER_CSREQ}", str(fake_signer.app)]


def test_verify_installed_reports_failure(fake_signer, tmp_path, monkeypatch):
    _fake_codesign(tmp_path, monkeypatch, 3, "test-requirement: code failed to satisfy\n")
    reason = signer.verify_installed()
    assert reason is not None and "exit 3" in reason and "failed to satisfy" in reason


def test_verify_installed_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(signer, "SIGNER_PATH", tmp_path / "nope")
    assert "not installed" in (signer.verify_installed() or "")


def test_verify_installed_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert signer.verify_installed() is not None


def test_csreq_is_the_requirement_without_comments():
    assert "/*" not in acl.SIGNER_CSREQ and "*/" not in acl.SIGNER_CSREQ
    assert acl.SIGNER_CSREQ.startswith(f'identifier "{acl.SIGNER_IDENTIFIER}" and anchor apple generic')
    assert acl._canonical_requirement(acl.SIGNER_CSREQ) == acl._canonical_requirement(acl.SIGNER_REQUIREMENT)


# --- SPEC section 9 --------------------------------------------------------

import os  # noqa: E402
from pathlib import Path  # noqa: E402


def test_implicit_metadata_read_is_allowed_but_not_more():
    req = {"contents": "read"}
    assert signer.widening_violation(req, [], {"contents": "read", "metadata": "read"}, "all", []) is None
    assert signer.widening_violation(req, [], {"metadata": "write"}, "all", [])
    assert signer.widening_violation(req, [], {"metadata": "admin"}, "all", [])
    # nothing requested: nothing to compare, as before
    assert signer.widening_violation({}, [], {"metadata": "write"}, "all", []) is None


def test_mint_accepts_implicit_metadata_and_refuses_metadata_write(fake_signer):
    fake_signer.set("mint", stdout=mint_output(permissions={"contents": "read", "metadata": "read"}))
    assert signer.mint("a", 1, perms={"contents": "read"}).token == TOKEN
    fake_signer.set("mint", stdout=mint_output(permissions={"contents": "read", "metadata": "write"}))
    with pytest.raises(signer.WidenedToken):
        signer.mint("a", 1, perms={"contents": "read"})


def test_installation_wrapper(fake_signer):
    fake_signer.set("installation", stdout={"id": 9, "account": {"login": "x"}, "permissions": {"contents": "read"}})
    assert signer.installation("a", 9)["account"]["login"] == "x"
    assert fake_signer.calls()[0]["argv"] == ["installation", "a", "9"]
    fake_signer.set("installation", exit=6, stderr={"error": "github", "message": "m", "status": 404})
    with pytest.raises(signer.GitHubError):
        signer.installation("a", 9)
    with pytest.raises(ValueError):
        signer.installation("a", 0)


def test_registration_failure_message_mentions_orphaned_key():
    from taaad.commands import register

    msg = register.store_failure_message("my-app", signer.BadKey("nope"))
    assert "live on GitHub but held by nobody" in msg
    assert "Private keys" in msg and "dated today" in msg
    assert "taaad rotate my-app" in msg and "nope" in msg


def _st(mode, uid=0):
    return os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))


REG, DIR, LNK = 0o100755, 0o040755, 0o120755
P = Path("/Library/Application Support/taaad/taaad-signer.app/Contents/MacOS/taaad-signer")


def fake_lstat(overrides=None):
    overrides = overrides or {}

    def f(p: str):
        if p in overrides:
            v = overrides[p]
            if isinstance(v, Exception):
                raise v
            return v
        return _st(REG if p == str(P) else DIR)

    return f


def test_path_problem_accepts_root_owned_tree():
    assert signer.path_problem(P, fake_lstat()) is None


@pytest.mark.parametrize(
    "target,st",
    [
        (str(P), _st(LNK)),  # symlink at the executable
        (str(P), _st(REG, uid=501)),  # owned by a user
        (str(P), _st(0o100775)),  # group-writable
        (str(P), _st(0o100757)),  # other-writable
        (str(P), _st(DIR)),  # not a regular file
        ("/Library/Application Support/taaad", _st(DIR, uid=501)),
        ("/Library/Application Support/taaad", _st(0o040775)),
        ("/Library/Application Support/taaad/taaad-signer.app/Contents", _st(LNK)),
        ("/Library/Application Support", _st(0o040757)),
        ("/Library", _st(0o040777)),
        (str(P), OSError("gone")),
    ],
)
def test_path_problem_rejects(target, st):
    assert signer.path_problem(P, fake_lstat({target: st})) is not None


def test_path_problem_stops_at_library():
    # /  being writable is not checked: the walk ends at /Library
    assert signer.path_problem(P, fake_lstat({"/": _st(0o040777, uid=501)})) is None


def test_path_problem_uses_lstat_by_default(tmp_path):
    f = tmp_path / "x"
    f.write_text("")
    assert signer.path_problem(f) is not None  # not root-owned


def test_run_refuses_unsafe_path_before_executing(fake_signer, monkeypatch):
    monkeypatch.setattr(signer, "PATH_CHECK", lambda p: "it is a symlink")
    with pytest.raises(signer.SignerMissing) as ei:
        signer.status("a")
    assert "symlink" in str(ei.value)
    assert fake_signer.calls() == []


def test_run_checks_the_path_on_every_call(fake_signer, monkeypatch):
    seen = []
    monkeypatch.setattr(signer, "PATH_CHECK", lambda p: seen.append(p))
    fake_signer.set("status", stdout={"present": False, "app_id": None})
    signer.status("a")
    signer.status("a")
    assert seen == [fake_signer.path, fake_signer.path]


def test_suite_never_reaches_a_real_installed_helper():
    """The autouse guard in conftest.py must keep the boundary away from /Library."""
    assert "/Library/" not in str(signer.SIGNER_PATH)
    assert not signer.available()
