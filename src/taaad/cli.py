"""taaad CLI entry point. Subcommands dispatch to per-action modules."""

from __future__ import annotations

import argparse
import sys

from taaad import __version__


def _add_register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("register", help="register a new GitHub App via manifest flow")
    p.add_argument("--gh-user", required=True)
    p.add_argument("--engagement", help="default-name slug; required unless --name is given")
    p.add_argument("--name", help="bot name (overrides --engagement)")
    p.add_argument("--org", help="register the App under an organization (default: under --gh-user)")


def _add_install(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("install", help="capture install ID for a registered app")
    p.add_argument("slug")
    p.add_argument("--account", required=True, help="org or user the install lives under")
    p.add_argument("--app-id", type=int, help="(optional) numeric App ID; self-discovered if absent")
    p.add_argument("--timeout", type=int, default=180, help="seconds to wait for install")


def _add_apps(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("apps", help="manage registered apps")
    s = p.add_subparsers(dest="apps_cmd", required=True)
    s.add_parser("list", help="list registered apps")
    show = s.add_parser("show", help="show app metadata")
    show.add_argument("slug")
    rm = s.add_parser("remove", help="remove an app's local config + keychain entry")
    rm.add_argument("slug")
    rm.add_argument("--ack-github-cleanup", action="store_true",
                    help="confirm you have deleted the App on github.com")


def _add_init(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="wire up the current repo to use a registered app")
    p.add_argument("--app", help="slug; if omitted, prompt with auto-detected default")


def _add_uninit(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("uninit", help="reverse `init` in the current repo")
    p.add_argument("--force", action="store_true", help="unset bot.* keys regardless of value match")


def _add_agent(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("agent", help="exec a CLI with the bot identity in env")
    p.add_argument("--app", help="override slug; default reads `bot.app` from cwd")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="command to exec")


def _add_env(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("env", help="print export statements for the bot identity")
    p.add_argument("--app", help="override slug; default reads `bot.app` from cwd")


def _add_credential_helper(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("credential-helper",
                       help="git-credential helper (called by git, not by humans)")
    p.add_argument("slug")
    p.add_argument("operation", nargs="?", default="get",
                   choices=["get", "store", "erase"])


def _add_rotate(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("rotate", help="rotate the PEM for an app")
    p.add_argument("slug")
    p.add_argument("--pem-file", required=True,
                   help="path to the new .pem downloaded from GitHub")


def _add_doctor(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("doctor", help="diagnose taaad setup")
    p.add_argument("--app", help="check a specific slug")


def _add_hooks(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("hooks", help="manage shared git hooks")
    s = p.add_subparsers(dest="hooks_cmd", required=True)
    s.add_parser("install", help="set core.hooksPath in the current repo")
    s.add_parser("uninstall", help="remove core.hooksPath if it points at taaad")


def _add_uninstall(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("uninstall", help="remove taaad config dir")
    p.add_argument("--purge", action="store_true",
                   help="also delete keychain entries")
    p.add_argument("--ack-github-cleanup", action="store_true",
                   help="confirm you have deleted Apps on github.com")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="taaad", description=__doc__)
    p.add_argument("--version", action="version", version=f"taaad {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    _add_register(sub)
    _add_install(sub)
    _add_apps(sub)
    _add_init(sub)
    _add_uninit(sub)
    _add_agent(sub)
    _add_env(sub)
    _add_credential_helper(sub)
    _add_rotate(sub)
    _add_doctor(sub)
    _add_hooks(sub)
    _add_uninstall(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command

    # Lazy imports keep `taaad --help` cheap and let one broken
    # subcommand not block the others.
    if cmd == "register":
        from taaad.commands import register
        return register.run(args)
    if cmd == "install":
        from taaad.commands import install
        return install.run(args)
    if cmd == "apps":
        from taaad.commands import apps
        return apps.run(args)
    if cmd == "init":
        from taaad.commands import init
        return init.run(args)
    if cmd == "uninit":
        from taaad.commands import uninit
        return uninit.run(args)
    if cmd == "agent":
        from taaad.commands import agent
        return agent.run(args)
    if cmd == "env":
        from taaad.commands import env
        return env.run(args)
    if cmd == "credential-helper":
        from taaad.commands import credential_helper
        return credential_helper.run(args)
    if cmd == "rotate":
        from taaad.commands import rotate
        return rotate.run(args)
    if cmd == "doctor":
        from taaad.commands import doctor
        return doctor.run(args)
    if cmd == "hooks":
        from taaad.commands import hooks
        return hooks.run(args)
    if cmd == "uninstall":
        from taaad.commands import uninstall_cmd
        return uninstall_cmd.run(args)
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2
