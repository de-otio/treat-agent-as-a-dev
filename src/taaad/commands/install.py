"""`taaad install <slug>` — capture install ID for a registered app.

Idempotent. If `apps/<slug>.toml` already has an `install_id`, this
verifies it still matches the current GitHub state; if changed, the
file is rewritten and a `previous_install_id` is recorded.

If the App was registered by an earlier taaad run, `app_id` and the
PEM are already on the machine. If the user has only the PEM (e.g.
re-enrolment from v0.4) and not the app_id, they pass `--app-id` or
the command discovers it via `GET /apps/<slug>` (using the PEM).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from taaad import config, github, secrets


def _ensure_app_config(slug: str, app_id_hint: int | None) -> config.AppConfig:
    """Return an AppConfig for `slug`, creating one if missing.

    For re-enrolment: PEM in keychain, no apps/<slug>.toml. Mint a
    JWT against `app_id_hint` (or fail and ask for --app-id) and
    confirm via GET /apps/<slug>. Then write a fresh apps/<slug>.toml.
    """
    keychain_key = config.keychain_key_for(slug)
    try:
        return config.read_app(slug)
    except FileNotFoundError:
        pass
    if not secrets.has_pem(keychain_key):
        raise SystemExit(
            f"no app config and no PEM at keychain key {keychain_key!r}. "
            f"Run `taaad register` first."
        )
    if app_id_hint is None:
        raise SystemExit(
            f"PEM found at {keychain_key} but no apps/{slug}.toml. "
            f"Pass --app-id <id> so we can discover the App. (Find "
            f"the ID at https://github.com/settings/apps/{slug}.)"
        )
    pem = secrets.get_pem(keychain_key)
    meta = github.get_app_by_slug(slug, pem, app_id_hint)
    pem = None  # noqa: F841 — discard
    return config.AppConfig(
        schema_version=config.SCHEMA_VERSION,
        slug=meta.slug,
        app_id=meta.id,
        install_id=None,
        account=None,
        keychain_key=keychain_key,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )


def run(args: argparse.Namespace) -> int:
    slug = args.slug
    config.ensure_dirs()
    cfg = _ensure_app_config(slug, args.app_id)

    pem = secrets.get_pem(cfg.keychain_key)
    print(
        f"polling for installation under @{args.account} (timeout {args.timeout}s)…",
        file=sys.stderr,
    )

    deadline = time.time() + args.timeout
    found = None
    while time.time() < deadline:
        found = github.find_installation(cfg.app_id, pem, args.account)
        if found:
            break
        time.sleep(5)
    pem = None  # discard

    if not found:
        print(
            f"no installation found for app_id={cfg.app_id} under "
            f"@{args.account}. Open the install URL "
            f"https://github.com/apps/{slug}/installations/new and "
            f"select the right account/repos.",
            file=sys.stderr,
        )
        return 1

    new_install_id = int(found["id"])
    new_account = found["account"]["login"]

    if cfg.install_id == new_install_id and cfg.account == new_account:
        print(
            f"install already recorded (id={new_install_id}, account={new_account}). "
            f"No change.",
            file=sys.stderr,
        )
        return 0

    updated = config.AppConfig(
        schema_version=cfg.schema_version,
        slug=cfg.slug,
        app_id=cfg.app_id,
        install_id=new_install_id,
        account=new_account,
        keychain_key=cfg.keychain_key,
        created_at=cfg.created_at,
    )
    config.write_app(updated)

    print(f"\n✅ Installation captured.")
    print(f"  slug:        {slug}")
    print(f"  install_id:  {new_install_id}")
    print(f"  account:     {new_account}")
    if cfg.install_id is not None and cfg.install_id != new_install_id:
        print(f"  previous:    {cfg.install_id} (replaced)")
    return 0
