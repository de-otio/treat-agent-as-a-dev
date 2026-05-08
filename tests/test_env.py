"""`taaad env` TTY guard. Refuses to print GH_TOKEN to a terminal
unless --force is passed, since stdout-to-TTY would land in
scrollback / shell history / screen-share captures.
"""

from __future__ import annotations

import argparse
import io
import sys

import pytest

from taaad.commands import env as env_mod


class _FakeTTY(io.StringIO):
    def isatty(self):
        return True


class _FakePipe(io.StringIO):
    def isatty(self):
        return False


@pytest.fixture
def args():
    return argparse.Namespace(app=None, force=False)


def test_refuses_when_stdout_is_tty(monkeypatch, args, capsys):
    monkeypatch.setattr(sys, "stdout", _FakeTTY())
    rc = env_mod.run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "TTY" in err
    assert "eval" in err


def test_force_bypasses_tty_guard(monkeypatch, args, capsys):
    """--force must allow printing even to a TTY (escape hatch)."""
    args.force = True
    monkeypatch.setattr(sys, "stdout", _FakeTTY())
    monkeypatch.setattr(
        env_mod.identity, "resolve_slug", lambda _explicit: "test-bot"
    )
    monkeypatch.setattr(
        env_mod.identity,
        "mint_token",
        lambda _slug: ("ghs_FAKE_TOKEN", _FakeAppCfg()),
    )
    monkeypatch.setattr(
        env_mod.identity,
        "env_for",
        lambda _s, t, _c: {"GH_TOKEN": t},
    )
    rc = env_mod.run(args)
    # We don't care about exact stdout (it's our fake), only that the
    # TTY guard didn't short-circuit.
    assert rc == 0


def test_pipe_emits_warning_to_stderr(monkeypatch, args, capsys):
    """When stdout is a pipe (the normal `eval $(...)` path), env
    should proceed AND emit a stderr warning so a developer who
    accidentally redirects to a file sees a hint."""
    monkeypatch.setattr(sys, "stdout", _FakePipe())
    monkeypatch.setattr(
        env_mod.identity, "resolve_slug", lambda _explicit: "test-bot"
    )
    monkeypatch.setattr(
        env_mod.identity,
        "mint_token",
        lambda _slug: ("ghs_FAKE_TOKEN", _FakeAppCfg()),
    )
    monkeypatch.setattr(
        env_mod.identity,
        "env_for",
        lambda _s, t, _c: {"GH_TOKEN": t},
    )
    rc = env_mod.run(args)
    assert rc == 0
    err = capsys.readouterr().err
    assert "GH_TOKEN" in err
    assert "Do not log" in err


class _FakeAppCfg:
    app_id = 12345
