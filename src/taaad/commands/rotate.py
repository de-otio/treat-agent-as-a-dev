"""`taaad rotate <slug> --pem-file <path>` — install a new PEM.

Order (plan 0001 §18): write to canonical key, mint a token to
verify, only then unlink the file. The PEM bytes are streamed via
keyring; this process holds them only between Path.read_bytes and
keyring.set_password.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from taaad import config, github, secrets
from taaad.slug import validate as validate_slug


def _shred(p: Path) -> None:
    """Best-effort overwrite + unlink. Not a guarantee against
    forensic recovery on SSDs, but better than a bare unlink."""
    try:
        size = p.stat().st_size
        with p.open("r+b") as f:
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        p.unlink()
    except OSError:
        pass


def run(args: argparse.Namespace) -> int:
    slug = validate_slug(args.slug)
    cfg = config.read_app(slug)

    pem_path = Path(args.pem_file).expanduser().resolve()
    if not pem_path.is_file():
        print(f"PEM file not found: {pem_path}", file=sys.stderr)
        return 1

    pem = pem_path.read_text()
    if "-----BEGIN" not in pem:
        print(f"file does not look like a PEM: {pem_path}", file=sys.stderr)
        return 1

    secrets.set_pem(cfg.keychain_key, pem)
    pem = None  # discard

    if cfg.install_id is not None:
        try:
            verify_pem = secrets.get_pem(cfg.keychain_key)
            token = github.installation_token(cfg.app_id, cfg.install_id, verify_pem)
            verify_pem = None  # noqa: F841
            if not token or not token.startswith("ghs_"):
                raise RuntimeError("token shape unexpected")
            token = None  # noqa: F841
            print("✅ token mints with new PEM.", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(
                f"❌ rotation FAILED on verification: {e}\n"
                f"   The new PEM is now in the keychain but does not "
                f"work. Either restore the previous PEM in the GitHub "
                f"App settings, or generate a new one and re-run "
                f"`taaad rotate`.",
                file=sys.stderr,
            )
            return 1

    _shred(pem_path)
    print(f"shredded {pem_path}", file=sys.stderr)
    print(
        "\n🛑 Now revoke the OLD private key in the App settings:\n"
        f"  https://github.com/settings/apps/{slug}\n"
        "  → Private keys → delete by date.",
        file=sys.stderr,
    )
    return 0
