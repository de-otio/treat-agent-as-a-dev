"""`taaad agent <cli> [args...]` — exec a child CLI with bot identity.

Mints a fresh token, scrubs trace env vars, validates the child
command against the allowlist (plan 0001 §13), then exec's.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from taaad import config, identity


def _check_allowlist(cmd: list[str], allowed: list[str]) -> None:
    # Guardrail, not a sandbox: this is a basename check, trivially
    # bypassable by symlinks or PATH manipulation. The point is to
    # prevent typos and accidental misuse ("oops, I meant claude not
    # claudine"), not to defend against a hostile user — anyone with
    # PATH or symlink control on this machine can already read
    # GH_TOKEN out of any child process's /proc/<pid>/environ.
    if not cmd:
        raise SystemExit("usage: taaad agent <cli> [args...]")
    base = os.path.basename(cmd[0])
    if base not in allowed:
        raise SystemExit(
            f"refusing to exec {base!r}: not in agent.allowed_commands "
            f"(currently {allowed}). Edit "
            f"{config.global_config_path()} to add commands."
        )


def _check_no_token_in_argv(argv: list[str], token: str) -> None:
    needle = token
    for a in argv:
        if needle and needle in a:
            raise SystemExit("refusing to exec: token literal found in argv")
        if "$GH_TOKEN" in a:
            raise SystemExit("refusing to exec: literal $GH_TOKEN in argv")


def run(args: argparse.Namespace) -> int:
    cmd = args.cmd or []
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("usage: taaad agent <cli> [args...]", file=sys.stderr)
        return 2

    allowed = config.allowed_commands()
    _check_allowlist(cmd, allowed)

    slug = identity.resolve_slug(args.app)
    token, cfg = identity.mint_token(slug)

    binary = shutil.which(cmd[0])
    if not binary:
        print(f"command not found: {cmd[0]}", file=sys.stderr)
        return 127

    _check_no_token_in_argv(cmd, token)

    env = dict(os.environ)
    identity.scrub_trace_env(env)
    env.update(identity.env_for(slug, token, cfg))

    os.execvpe(binary, [binary, *cmd[1:]], env)
    return 0  # unreachable
