from __future__ import annotations

import argparse
import sys
import webbrowser

from taaad import config, secrets


def _list() -> int:
    apps = config.list_apps()
    if not apps:
        print("(no apps registered — run `taaad register`)", file=sys.stderr)
        return 0
    for a in apps:
        print(a.slug)
    return 0


def _show(slug: str) -> int:
    a = config.read_app(slug)
    print(f"slug:           {a.slug}")
    print(f"app_id:         {a.app_id}")
    print(f"install_id:     {a.install_id if a.install_id is not None else '(unknown — run `taaad install`)'}")
    print(f"account:        {a.account or '(unknown)'}")
    print(f"keychain_key:   {a.keychain_key}")
    print(f"keychain_pres.: {'yes' if secrets.has_pem(a.keychain_key) else 'NO'}")
    print(f"created_at:     {a.created_at}")
    return 0


def _remove(slug: str, ack: bool) -> int:
    a = config.read_app(slug)
    if not ack:
        print(
            f"\n🛑 manual step: delete the App on github.com first.\n"
            f"   1. https://github.com/settings/installations\n"
            f"      → uninstall {slug} from {a.account or '(account)'}\n"
            f"   2. https://github.com/settings/apps/{slug}/advanced\n"
            f"      → Delete GitHub App\n"
            f"\n   GitHub does not expose an App-deletion API for\n"
            f"   user-owned Apps; this step is mandatory and manual.\n"
            f"\n   Re-run with --ack-github-cleanup once done.\n",
            file=sys.stderr,
        )
        try:
            webbrowser.open(f"https://github.com/settings/apps/{slug}/advanced")
        except Exception:  # noqa: BLE001
            pass
        return 3
    secrets.delete_pem(a.keychain_key)
    config.remove_app(slug)
    used_by = config.used_by_path(slug)
    if used_by.exists():
        used_by.unlink()
    print(f"removed {slug} (config + keychain entry).", file=sys.stderr)
    return 0


def run(args: argparse.Namespace) -> int:
    if args.apps_cmd == "list":
        return _list()
    if args.apps_cmd == "show":
        return _show(args.slug)
    if args.apps_cmd == "remove":
        return _remove(args.slug, args.ack_github_cleanup)
    print(f"unknown apps subcommand {args.apps_cmd}", file=sys.stderr)
    return 2
