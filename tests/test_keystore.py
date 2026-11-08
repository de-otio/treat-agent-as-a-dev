"""Routing between the signer helper and the legacy keyring, and the
call sites that depend on it (identity, install, rotate, apps, doctor).

The helper is a fake (tests/fake_signer.py). Wherever a test must prove
that the legacy path is not touched, the legacy entry points are patched
to raise."""

from __future__ import annotations

import argparse
import sys

import keyring
import pytest

from taaad import acl, config, github, identity, keystore, secrets, signer
from taaad.commands import apps, doctor, install, rotate, uninstall_cmd
from tests.fake_signer import TOKEN, fake_signer, mint_output  # noqa: F401

PEM = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n"
SLUG = "my-app"
KEY = "github-app-my-app-pem"
LEGACY_TOKEN = "ghs_legacytoken"


def cfg(install_id: int | None = 99) -> config.AppConfig:
    return config.AppConfig(
        schema_version=config.SCHEMA_VERSION, slug=SLUG, app_id=7, install_id=install_id,
        account="example-org", keychain_key=KEY, created_at="2026-01-01T00:00:00+00:00",
    )


def holds_key(fake, present: bool = True) -> None:
    fake.set("status", stdout={"slug": SLUG, "present": present, "app_id": 7 if present else None})


def legacy_forbidden(monkeypatch) -> None:
    """Any touch of the keyring or the direct GitHub client fails the test."""
    def boom(*a, **kw):
        raise AssertionError("legacy path touched")
    for mod, names in {
        secrets: ["get_pem", "set_pem", "has_pem", "delete_pem"],
        github: ["installation_token", "get_app_meta", "list_installations", "app_jwt"],
    }.items():
        for n in names:
            monkeypatch.setattr(mod, n, boom)
    monkeypatch.setattr(keyring, "get_password", boom)
    monkeypatch.setattr(keyring, "set_password", boom)


def legacy_item() -> None:
    secrets.set_pem(KEY, PEM)


@pytest.fixture
def legacy_github(monkeypatch):
    monkeypatch.setattr(github, "installation_token", lambda *a: LEGACY_TOKEN)
    monkeypatch.setattr(github, "get_app_meta", lambda *a: {"public": False, "via": "legacy"})
    monkeypatch.setattr(github, "list_installations", lambda *a: [{"id": 5, "account": {"login": "x"}}])


# --- routing ---------------------------------------------------------------


def test_signer_used_when_macos_installed_and_key_held(fake_signer, monkeypatch):
    holds_key(fake_signer)
    fake_signer.set("mint", stdout=mint_output())
    fake_signer.set("app-meta", stdout={"public": False})
    fake_signer.set("installations", stdout=[{"id": 1, "account": {"login": "x"}}])
    legacy_forbidden(monkeypatch)
    assert keystore.mint(cfg()) == TOKEN
    assert keystore.app_meta(cfg()) == {"public": False}
    assert keystore.installations(cfg()) == [{"id": 1, "account": {"login": "x"}}]
    assert keystore.describe(cfg()) == "taaad-signer"
    assert keystore.has_key(cfg()) is True


def test_legacy_used_when_helper_holds_no_key(fake_signer, legacy_github):
    holds_key(fake_signer, present=False)
    legacy_item()
    assert keystore.mint(cfg()) == LEGACY_TOKEN
    assert keystore.app_meta(cfg())["via"] == "legacy"
    assert keystore.describe(cfg()) == "keychain (readable by Python)"
    assert set(fake_signer.commands()) == {"status"}


def test_legacy_used_when_helper_not_installed(tmp_path, monkeypatch, legacy_github):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(signer, "SIGNER_PATH", tmp_path / "absent")
    legacy_item()
    assert keystore.mint(cfg()) == LEGACY_TOKEN


def test_legacy_used_off_macos_and_helper_never_run(fake_signer, monkeypatch, legacy_github):
    monkeypatch.setattr(sys, "platform", "linux")
    legacy_item()
    assert keystore.mint(cfg()) == LEGACY_TOKEN
    assert keystore.describe(cfg()) == "keychain (readable by Python)"
    assert fake_signer.calls() == []


def test_describe_missing(fake_signer):
    holds_key(fake_signer, present=False)
    assert keystore.describe(cfg()) == "missing"
    assert keystore.has_key(cfg()) is False


# --- no fallback -----------------------------------------------------------

MINT_FAILURES = [
    (4, signer.PresenceDenied, {}),
    (5, signer.RateLimited, {"retry_after": 30}),
    (6, signer.GitHubError, {"status": 500}),
    (7, signer.WidenedToken, {}),
    (8, signer.KeychainError, {"osstatus": -1}),
    (3, signer.KeyNotFound, {}),
]


@pytest.mark.parametrize("code,cls,extra", MINT_FAILURES)
def test_signer_error_never_falls_back(fake_signer, monkeypatch, code, cls, extra):
    legacy_item()  # a Python-readable copy exists and must not be used
    holds_key(fake_signer)
    fake_signer.set("mint", exit=code, stdout={"token": TOKEN},
                    stderr={"error": "x", "message": "nope", **extra})
    # from here, touching the keyring or GitHub client fails the test
    legacy_forbidden(monkeypatch)
    with pytest.raises(cls) as ei:
        keystore.mint(cfg())
    assert TOKEN not in str(ei.value)


def test_presence_denied_via_identity_is_a_clean_exit(fake_signer, tmp_config_dir, monkeypatch):
    config.write_app(cfg())
    holds_key(fake_signer)
    fake_signer.set("mint", exit=4, stdout={"token": TOKEN},
                    stderr={"error": "presence-denied", "message": "cancelled"})
    legacy_forbidden(monkeypatch)
    with pytest.raises(SystemExit) as ei:
        identity.mint_token(SLUG)
    assert "cancelled" in str(ei.value) and TOKEN not in str(ei.value)


def test_identity_mint_token_success(fake_signer, tmp_config_dir):
    config.write_app(cfg())
    holds_key(fake_signer)
    fake_signer.set("mint", stdout=mint_output())
    token, c = identity.mint_token(SLUG)
    assert token == TOKEN and c.slug == SLUG


def test_failing_status_is_an_error_not_a_no(fake_signer, monkeypatch):
    legacy_item()
    fake_signer.set("status", exit=8, stderr={"error": "keychain", "message": "locked", "osstatus": -25293})
    legacy_forbidden(monkeypatch)
    with pytest.raises(signer.KeychainError):
        keystore.mint(cfg())


def test_widened_helper_output_is_refused_through_keystore(fake_signer, monkeypatch):
    holds_key(fake_signer)
    fake_signer.set("mint", stdout=mint_output(permissions={"contents": "write"}))
    legacy_forbidden(monkeypatch)
    # keystore.mint asks for nothing in particular, so nothing can widen;
    # the check bites when the call narrows.
    assert keystore.mint(cfg()) == TOKEN
    with pytest.raises(signer.WidenedToken):
        signer.mint(SLUG, 99, perms={"contents": "read"})


# --- store_new / delete ----------------------------------------------------


def test_store_new_goes_to_signer_when_installed(fake_signer, monkeypatch):
    fake_signer.set("import", stdout={"slug": SLUG, "app_id": 7, "app_slug": SLUG, "replaced": False})
    legacy_forbidden(monkeypatch)
    assert keystore.store_new(SLUG, 7, PEM, KEY) == "signer"
    assert fake_signer.calls()[0]["argv"] == ["import", SLUG, "7"]


@pytest.mark.parametrize("code,cls", [(9, signer.KeyExists), (10, signer.BadKey), (4, signer.PresenceDenied)])
def test_store_new_signer_error_does_not_write_keyring(fake_signer, monkeypatch, code, cls):
    fake_signer.set("import", exit=code, stderr={"error": "x", "message": "m"})
    legacy_forbidden(monkeypatch)
    with pytest.raises(cls):
        keystore.store_new(SLUG, 7, PEM, KEY)


def test_store_new_uses_keyring_without_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert keystore.store_new(SLUG, 7, PEM, KEY) == "keyring"
    assert secrets.has_pem(KEY)


def test_delete_removes_both_stores(fake_signer):
    legacy_item()
    holds_key(fake_signer)
    fake_signer.set("delete", stdout={"slug": SLUG, "deleted": True})
    keystore.delete(cfg())
    assert "delete" in fake_signer.commands()
    assert not secrets.has_pem(KEY)


def test_delete_keeps_legacy_when_signer_delete_fails(fake_signer):
    legacy_item()
    holds_key(fake_signer)
    fake_signer.set("delete", exit=4, stderr={"error": "presence-denied", "message": "no"})
    with pytest.raises(signer.PresenceDenied):
        keystore.delete(cfg())
    assert secrets.has_pem(KEY)


def test_delete_legacy_only(fake_signer):
    holds_key(fake_signer, present=False)
    legacy_item()
    keystore.delete(cfg())
    assert "delete" not in fake_signer.commands()
    assert not secrets.has_pem(KEY)


# --- install: pure account matching ---------------------------------------


def test_match_installation_is_pure_and_exact():
    items = [{"id": 1, "account": {"login": "Alice"}}, {"id": 2, "account": {"login": "bob"}},
             {"id": 3}, {"id": 4, "account": None}]
    assert install.match_installation(items, "bob")["id"] == 2
    assert install.match_installation(items, "alice") is None
    assert install.match_installation([], "bob") is None


def test_install_run_uses_signer_list(fake_signer, tmp_config_dir, monkeypatch, capsys):
    config.write_app(cfg(install_id=None))
    holds_key(fake_signer)
    fake_signer.set("installations", stdout=[{"id": 55, "account": {"login": "example-org"}}])
    legacy_forbidden(monkeypatch)
    rc = install.run(argparse.Namespace(slug=SLUG, app_id=None, account="example-org", timeout=5))
    assert rc == 0
    assert config.read_app(SLUG).install_id == 55


def test_install_run_reports_signer_error(fake_signer, tmp_config_dir, monkeypatch, capsys):
    config.write_app(cfg(install_id=None))
    holds_key(fake_signer)
    fake_signer.set("installations", exit=6, stderr={"error": "github", "message": "boom", "status": 401})
    legacy_forbidden(monkeypatch)
    assert install.run(argparse.Namespace(slug=SLUG, app_id=None, account="x", timeout=5)) == 1
    assert "boom" in capsys.readouterr().err


# --- rotate ----------------------------------------------------------------


def rotate_args(tmp_path):
    f = tmp_path / "new.pem"
    f.write_text(PEM)
    return f, argparse.Namespace(slug=SLUG, pem_file=str(f))


def test_rotate_signer_path_success(fake_signer, tmp_config_dir, tmp_path):
    config.write_app(cfg())
    legacy_item()
    holds_key(fake_signer)  # key exists -> replace
    fake_signer.set("import", stdout={"slug": SLUG, "app_id": 7, "app_slug": SLUG, "replaced": True})
    fake_signer.set("mint", stdout=mint_output())
    f, args = rotate_args(tmp_path)
    assert rotate.run(args) == 0
    cmds = fake_signer.calls()
    assert [c["argv"][0] for c in cmds] == ["status", "import", "mint"]
    assert "--replace" in cmds[1]["argv"]
    assert not secrets.has_pem(KEY)  # legacy copy removed
    assert not f.exists()  # PEM file shredded


def test_rotate_first_import_has_no_replace(fake_signer, tmp_config_dir, tmp_path):
    config.write_app(cfg(install_id=None))
    holds_key(fake_signer, present=False)
    fake_signer.set("import", stdout={"slug": SLUG, "app_id": 7, "app_slug": SLUG, "replaced": False})
    f, args = rotate_args(tmp_path)
    assert rotate.run(args) == 0
    assert "--replace" not in fake_signer.calls()[1]["argv"]
    assert "mint" not in fake_signer.commands()


@pytest.mark.parametrize("failing", ["import", "mint"])
def test_rotate_failure_leaves_file_and_legacy_copy(fake_signer, tmp_config_dir, tmp_path, monkeypatch, failing):
    config.write_app(cfg())
    legacy_item()
    holds_key(fake_signer)
    fake_signer.set("import", stdout={"slug": SLUG, "app_id": 7, "app_slug": SLUG, "replaced": True})
    fake_signer.set("mint", stdout=mint_output())
    fake_signer.set(failing, exit=4, stderr={"error": "presence-denied", "message": "cancelled"})
    f, args = rotate_args(tmp_path)
    assert rotate.run(args) == 1
    assert f.exists()
    assert secrets.has_pem(KEY)


def test_rotate_keyring_path_without_helper(tmp_config_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    config.write_app(cfg(install_id=None))
    f, args = rotate_args(tmp_path)
    assert rotate.run(args) == 0
    assert secrets.has_pem(KEY) and not f.exists()


# --- apps / uninstall ------------------------------------------------------


def test_apps_show_names_the_holder(fake_signer, tmp_config_dir, capsys):
    config.write_app(cfg())
    holds_key(fake_signer)
    apps._show(SLUG)
    assert "taaad-signer" in capsys.readouterr().out


def test_apps_remove_signer_failure_keeps_config(fake_signer, tmp_config_dir, capsys):
    config.write_app(cfg())
    holds_key(fake_signer)
    fake_signer.set("delete", exit=4, stderr={"error": "presence-denied", "message": "no"})
    assert apps._remove(SLUG, True) == 1
    assert config.app_path(SLUG).exists()


def test_apps_remove_deletes_both(fake_signer, tmp_config_dir):
    config.write_app(cfg())
    legacy_item()
    holds_key(fake_signer)
    fake_signer.set("delete", stdout={"slug": SLUG, "deleted": True})
    assert apps._remove(SLUG, True) == 0
    assert not config.app_path(SLUG).exists() and not secrets.has_pem(KEY)


def test_uninstall_purge_deletes_via_keystore(fake_signer, tmp_config_dir):
    config.write_app(cfg())
    legacy_item()
    holds_key(fake_signer)
    fake_signer.set("delete", stdout={"slug": SLUG, "deleted": True})
    rc = uninstall_cmd.run(argparse.Namespace(purge=True, ack_github_cleanup=True))
    assert rc == 0
    assert "delete" in fake_signer.commands() and not secrets.has_pem(KEY)


def test_uninstall_purge_keeps_config_when_signer_delete_fails(fake_signer, tmp_config_dir):
    config.write_app(cfg())
    holds_key(fake_signer)
    fake_signer.set("delete", exit=4, stderr={"error": "presence-denied", "message": "no"})
    rc = uninstall_cmd.run(argparse.Namespace(purge=True, ack_github_cleanup=True))
    assert rc == 1 and config.app_path(SLUG).exists()


# --- doctor ----------------------------------------------------------------


def test_doctor_flags_leftover_legacy_item(fake_signer, capsys, monkeypatch):
    legacy_item()
    holds_key(fake_signer)
    fake_signer.set("app-meta", stdout={"public": False})
    fails = doctor._check_app(cfg())
    out = capsys.readouterr()
    assert fails == 1
    assert "[ok]  key held by taaad-signer" in out.out
    assert "legacy copy still readable by Python" in out.err and "taaad rotate" in out.err
    assert "mint" not in fake_signer.commands()  # app-meta, not a mint (no prompt)


def test_doctor_signer_only_is_clean(fake_signer, capsys, monkeypatch):
    holds_key(fake_signer)
    fake_signer.set("app-meta", stdout={"public": False})
    monkeypatch.setattr(secrets, "read_acl", lambda k: pytest.fail("ACL check is for legacy keys"))
    assert doctor._check_app(cfg()) == 0
    assert "mint" not in fake_signer.commands()


def test_doctor_flags_public_app_on_signer_path(fake_signer, capsys):
    holds_key(fake_signer)
    fake_signer.set("app-meta", stdout={"public": True})
    assert doctor._check_app(cfg()) == 1
    assert "PUBLIC" in capsys.readouterr().err


def test_doctor_reports_signer_error(fake_signer, capsys):
    holds_key(fake_signer)
    fake_signer.set("app-meta", exit=6, stderr={"error": "github", "message": "bad", "status": 401})
    assert doctor._check_app(cfg()) == 1
    assert "bad" in capsys.readouterr().err


def test_doctor_without_signer_key_runs_the_acl_check(fake_signer, capsys, monkeypatch, legacy_github):
    holds_key(fake_signer, present=False)
    legacy_item()
    calls = []
    monkeypatch.setattr(doctor, "_check_pem_acl", lambda a: calls.append(a.slug) or 0)
    doctor._check_app(cfg())
    assert calls == [SLUG]


def test_doctor_signer_check(fake_signer, capsys, monkeypatch):
    monkeypatch.setattr(signer, "verify_installed", lambda: None)
    assert doctor._check_signer() == 0
    monkeypatch.setattr(signer, "verify_installed", lambda: "codesign --verify failed (exit 3)")
    assert doctor._check_signer() == 1
    assert "exit 3" in capsys.readouterr().err


def test_doctor_signer_check_skipped_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(signer, "verify_installed", lambda: pytest.fail("not on linux"))
    assert doctor._check_signer() == 0
