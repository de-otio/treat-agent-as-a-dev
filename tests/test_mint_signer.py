"""`taaad mint` through the signer helper (fake helper; SPEC section 9,
items 13-14). Legacy entry points are patched to raise, so a test fails
if the command touches the keyring or GitHub directly."""

from __future__ import annotations

import argparse
import io
import json
import sys

import pytest

from taaad import cli, config, github, secrets
from taaad.commands import mint as mint_mod
from tests.fake_signer import TOKEN, fake_signer, mint_output  # noqa: F401

# The helper is macOS-only; these tests run a POSIX fake helper.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="taaad-signer is macOS-only; tests use a POSIX fake helper")

SLUG = "my-app"
GRANTED = {"metadata": "read", "contents": "write", "pull_requests": "write", "issues": "write"}
INSTALLATION = {"account": {"login": "Example-Org"}, "permissions": GRANTED}


def args(repo="example-org/widgets", kind="read"):
    return argparse.Namespace(app=SLUG, repo=repo, permissions=kind, json=True)


@pytest.fixture
def world(fake_signer, tmp_config_dir, monkeypatch):
    config.write_app(config.AppConfig(1, SLUG, 777, 4242, "Example-Org",
                                      f"github-app-{SLUG}-pem", "t"))
    fake_signer.set("status", stdout={"slug": SLUG, "present": True, "app_id": 777})
    fake_signer.set("installation", stdout=INSTALLATION)

    def boom(*a, **kw):
        raise AssertionError("legacy path touched")

    for mod, n in [(secrets, "get_pem"), (github, "app_jwt"), (github, "get_installation"),
                   (github, "create_scoped_token")]:
        monkeypatch.setattr(mod, n, boom)
    return fake_signer


READ_PERMS = {"metadata": "read", "contents": "read", "pull_requests": "read", "issues": "read"}


def good_mint(**over):
    return mint_output(permissions=READ_PERMS, repository_selection="selected",
                       repositories=["widgets"], account="Example-Org", **over)


def test_success_contract(world, capsys):
    world.set("mint", stdout=good_mint())
    assert mint_mod.run(args()) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"token", "expiresAt", "installationAccount", "installationId",
                        "appId", "repositories", "permissions"}
    assert out["token"] == TOKEN and out["repositories"] == ["widgets"]
    assert out["installationAccount"] == "Example-Org" and out["installationId"] == 4242
    assert out["appId"] == 777 and out["permissions"] == READ_PERMS
    mint_call = [c for c in world.calls() if c["argv"][0] == "mint"][0]["argv"]
    assert mint_call[:5] == ["mint", SLUG, "4242", "--repo", "widgets"]
    assert "contents=read" in mint_call and "--perm" in mint_call


def test_owner_mismatch_refused_before_any_prompt(world, capsys):
    world.set("mint", stdout=good_mint())
    assert mint_mod.run(args(repo="someone-else/widgets")) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "someone-else" in cap.err
    assert "mint" not in world.commands()


def test_widened_helper_output_is_refused(world, capsys):
    world.set("mint", stdout=good_mint() | {"permissions": {**READ_PERMS, "contents": "write"}})
    assert mint_mod.run(args()) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and TOKEN not in cap.err


def test_helper_widened_exit_is_one(world, capsys):
    world.set("mint", exit=7, stdout={"token": TOKEN},
              stderr={"error": "widened", "message": "wider"})
    assert mint_mod.run(args()) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and TOKEN not in cap.err


def test_extra_repository_in_response_is_refused(world, capsys):
    world.set("mint", stdout=good_mint() | {"repositories": ["widgets", "other"]})
    assert mint_mod.run(args()) == 1
    assert capsys.readouterr().out == ""


def test_rate_limited_exits_3(world, capsys):
    world.set("mint", exit=5, stdout={"token": TOKEN},
              stderr={"error": "rate-limited", "message": "slow down", "retry_after": 40})
    assert mint_mod.run(args()) == 3
    cap = capsys.readouterr()
    assert cap.out == "" and TOKEN not in cap.err


def test_presence_denied_exits_4(world, capsys):
    world.set("mint", exit=4, stdout={"token": TOKEN},
              stderr={"error": "presence-denied", "message": "cancelled"})
    assert mint_mod.run(args()) == 4
    cap = capsys.readouterr()
    assert cap.out == "" and TOKEN not in cap.err


@pytest.mark.parametrize("code", [1, 6, 8, 3])
def test_other_helper_errors_exit_1(world, capsys, code):
    world.set("mint", exit=code, stderr={"error": "x", "message": "m", "status": 500})
    assert mint_mod.run(args()) == 1
    assert capsys.readouterr().out == ""


def test_installation_failure_exits_1(world, capsys):
    world.set("installation", exit=6, stderr={"error": "github", "message": "gone", "status": 404})
    assert mint_mod.run(args()) == 1
    assert capsys.readouterr().out == ""


def test_tty_refusal_is_2_with_empty_stdout(world, monkeypatch, capsys):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdout", TTY())
    assert mint_mod.run(args()) == 2
    assert world.calls() == []


def test_contents_write_sends_contents_write_and_metadata(world, capsys):
    world.set("mint", stdout=mint_output(permissions={"contents": "write", "metadata": "read"},
                                         repository_selection="selected", repositories=["widgets"]))
    assert mint_mod.run(args(kind="contents-write")) == 0
    argv = [c for c in world.calls() if c["argv"][0] == "mint"][0]["argv"]
    assert "contents=write" in argv and "metadata=read" in argv


def test_help_documents_exit_codes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["mint", "--help"])
    out = capsys.readouterr().out
    for line in ("2  refused: stdout is a TTY", "3  rate-limited", "4  presence prompt", "1  any other"):
        assert line in out


def test_readme_documents_exit_codes():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "README.md").read_text()
    assert "rate-limited by `taaad-signer`" in text and "presence prompt cancelled" in text
