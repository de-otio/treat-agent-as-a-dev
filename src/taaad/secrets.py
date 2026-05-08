"""Secret-store wrapper around `keyring`.

Each app's PEM is stored under service=<keychain_key>,
username=<os user>. This matches v0.4's
`security add-generic-password -s <key> -a $USER` convention so
existing entries are reused without read-then-rewrite (preserves
RUNBOOK Operating Rule 1).

Refuses to operate against plaintext-fallback backends — see plan
0001 §7. PEM bytes are kept in memory only as long as needed.
"""

from __future__ import annotations

import os
import sys

import keyring
import keyring.backend
import keyring.errors


def _username() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "taaad"


def _backend_name() -> str:
    b = keyring.get_keyring()
    return f"{type(b).__module__}.{type(b).__name__}"


def assert_safe_backend() -> None:
    name = _backend_name()
    lower = name.lower()
    if "plaintext" in lower or "null" in lower or "fail" in lower:
        raise RuntimeError(
            f"keyring backend {name} is unsafe for PEM storage. "
            f"Start a Secret Service daemon (gnome-keyring-daemon, "
            f"kwallet) or install `pass`."
        )


def set_pem(keychain_key: str, pem: str) -> None:
    assert_safe_backend()
    keyring.set_password(keychain_key, _username(), pem)


def get_pem(keychain_key: str) -> str:
    assert_safe_backend()
    pem = keyring.get_password(keychain_key, _username())
    if pem is None or not pem.strip():
        raise RuntimeError(
            f"no PEM at keychain key {keychain_key!r} for user "
            f"{_username()!r}. On Linux, check that your session "
            f"keyring is unlocked."
        )
    return _maybe_dehex(pem)


def _maybe_dehex(value: str) -> str:
    """macOS `security ... -w` returns ASCII hex when the stored
    value contains newlines (which any PEM does). Decode
    transparently so callers don't need to know how the keychain
    backend encoded it.
    """
    import binascii

    stripped = value.strip()
    if stripped and len(stripped) % 2 == 0 and all(
        c in "0123456789abcdefABCDEF" for c in stripped
    ):
        try:
            return binascii.unhexlify(stripped).decode()
        except (binascii.Error, UnicodeDecodeError):
            return value
    return value


def has_pem(keychain_key: str) -> bool:
    try:
        assert_safe_backend()
    except RuntimeError:
        return False
    try:
        v = keyring.get_password(keychain_key, _username())
    except keyring.errors.KeyringError:
        return False
    return v is not None and bool(v.strip())


def delete_pem(keychain_key: str) -> None:
    assert_safe_backend()
    try:
        keyring.delete_password(keychain_key, _username())
    except keyring.errors.PasswordDeleteError:
        pass


def backend_info() -> str:
    return _backend_name()


def restrict_macos_acl(keychain_key: str, taaad_path: str) -> None:
    """Best-effort: tighten the macOS Keychain ACL to a single binary.

    Non-Darwin platforms and `security` errors are swallowed — the
    keyring already wrote the entry under the default ACL.
    """
    if sys.platform != "darwin":
        return
    import subprocess

    try:
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s", keychain_key,
                "-a", _username(),
                "-T", taaad_path,
            ],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass
