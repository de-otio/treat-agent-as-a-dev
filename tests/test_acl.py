"""Keychain ACL parsing and verdict.

Fixtures under tests/fixtures are redacted `security dump-keychain -a`
output (slugs, home path and code hashes replaced). The first two are
shaped like real items observed on a macOS machine; the signer-only
one is synthetic because the helper does not exist yet.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from taaad import acl, secrets
from taaad.commands import doctor
from taaad import config

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIX / name).read_text()


SVC_B = "github-app-example-b-pem"
SVC_A = "github-app-example-a-pem"
SVC_C = "github-app-example-c-pem"

SIGNER_REQ = (
    f'identifier "{acl.SIGNER_IDENTIFIER}" and anchor apple generic '
    f'and certificate leaf[subject.OU] = "{acl.SIGNER_TEAM_ID}"'
)


def report(*apps: acl.TrustedApp, any_app=False, partitions=frozenset()) -> acl.AclReport:
    return acl.AclReport("svc", "me", tuple(apps), any_app, frozenset(partitions))


# --- parsing -------------------------------------------------------------


def test_parse_interpreter_and_launcher_item():
    (r,) = acl.parse_acl(load("acl_python_and_launcher.txt"), SVC_B)
    assert r.account == "testuser"
    paths = [a.path for a in r.apps]
    assert len(paths) == 3
    assert paths[2] == "/Users/testuser/.local/pipx/venvs/taaad/bin/taaad"
    assert r.apps[2].requirement == "none"
    assert r.apps[0].requirement.startswith("cdhash")
    assert not r.any_app
    assert r.partitions == {"cdhash"}


def test_parse_security_item_sees_apple_tool_partition():
    (r,) = acl.parse_acl(load("acl_python_and_security.txt"), SVC_A)
    assert "/usr/bin/security" in [a.path for a in r.apps]
    assert "apple-tool" in r.partitions


def test_encrypt_entry_null_applications_is_not_any_app():
    """Entry 0 of every item is `encrypt` with `applications: <null>`.
    That is not read access and must not flag the item."""
    (r,) = acl.parse_acl(load("acl_signer_only_synthetic.txt"), SVC_C)
    assert not r.any_app


def test_selects_only_the_requested_service():
    dump = load("acl_unrelated_item.txt") + load("acl_python_and_launcher.txt")
    assert acl.parse_acl(dump, "some-other-service")[0].service == "some-other-service"
    (r,) = acl.parse_acl(dump, SVC_B)
    assert r.service == SVC_B
    assert acl.parse_acl(dump, "absent") == []


def test_path_with_spaces_and_parentheses():
    dump = load("acl_signer_only_synthetic.txt").replace(
        "/usr/local/libexec/taaad-signer", "/Applications/My Tool (beta)/bin/x y"
    )
    (r,) = acl.parse_acl(dump, SVC_C)
    assert r.apps[0].path == "/Applications/My Tool (beta)/bin/x y"


def test_read_entry_with_null_applications_means_any_app():
    dump = load("acl_python_and_launcher.txt")
    start = dump.index("applications (3):")
    end = dump.index("    entry 2:")
    dump = dump[:start] + "applications: <null>\n" + dump[end:]
    (r,) = acl.parse_acl(dump, SVC_B)
    assert r.any_app
    assert r.apps == ()


# --- verdict -------------------------------------------------------------


def codes(findings):
    return " | ".join(f.reason for f in findings)


def test_interpreter_and_path_only_launcher_fail():
    findings = acl.verdict(acl.parse_acl(load("acl_python_and_launcher.txt"), SVC_B))
    text = codes(findings)
    assert "interpreter" in text
    assert "trusted by path only" in text


def test_security_tool_and_apple_partition_fail():
    findings = acl.verdict(acl.parse_acl(load("acl_python_and_security.txt"), SVC_A))
    text = codes(findings)
    assert "/usr/bin/security" in text
    assert "Apple-signed tools" in text


def test_signer_only_passes():
    assert acl.verdict(acl.parse_acl(load("acl_signer_only_synthetic.txt"), SVC_C)) == []


def test_signer_path_without_code_requirement_fails():
    dump = load("acl_signer_only_synthetic.txt").replace(
        next(l for l in load("acl_signer_only_synthetic.txt").splitlines() if "requirement:" in l),
        "                requirement: none",
    )
    assert acl.verdict(acl.parse_acl(dump, SVC_C))


def test_wrong_team_id_fails():
    dump = load("acl_signer_only_synthetic.txt").replace(acl.SIGNER_TEAM_ID, "AAAAAAAAAA")
    assert acl.verdict(acl.parse_acl(dump, SVC_C))


def test_signer_plus_interpreter_fails():
    r = report(acl.TrustedApp("/usr/local/libexec/taaad-signer", SIGNER_REQ),
               acl.TrustedApp("/opt/homebrew/bin/python3", 'cdhash H"aa"'))
    assert acl.verdict([r])


def test_absent_item_is_a_finding():
    assert acl.verdict([])


def test_item_with_no_trusted_apps_is_a_finding():
    assert acl.verdict([report()])


def test_any_app_fails_even_with_signer():
    r = report(acl.TrustedApp("/x/taaad-signer", SIGNER_REQ), any_app=True)
    assert acl.verdict([r])


apps = st.builds(acl.TrustedApp, path=st.text(min_size=1), requirement=st.text())


@given(st.lists(apps, max_size=6))
def test_property_only_signers_can_pass(app_list):
    """Allowlist: if any trusted app is not the signed helper, the item fails."""
    r = report(*app_list)
    findings = acl.verdict([r])
    if findings == []:
        assert app_list and all(acl.is_signer(a) for a in app_list)


@given(apps, st.lists(apps, max_size=4))
def test_property_one_foreign_app_always_fails(foreign, others):
    assume(not acl.is_signer(foreign))
    signer = acl.TrustedApp("/x/taaad-signer", SIGNER_REQ)
    assert acl.verdict([report(signer, foreign, *others)])


@given(st.text(max_size=200), st.text(max_size=40))
def test_property_parser_never_raises(text, service):
    acl.parse_acl(text, service)


# --- shell wrapper and doctor -------------------------------------------


def _fake_run(stdout: str, returncode: int = 0, seen: list | None = None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")
    return run


def test_read_acl_never_decrypts(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    seen: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(load("acl_python_and_launcher.txt"), seen=seen))
    (r,) = secrets.read_acl(SVC_B)
    assert r.service == SVC_B
    cmd = seen[0]
    assert cmd[:3] == ["security", "dump-keychain", "-a"]
    assert "-d" not in cmd and "-w" not in cmd


def test_read_acl_nonzero_exit_raises_without_echoing_output(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_run("SECRET-LOOKING-TEXT", returncode=2))
    with pytest.raises(RuntimeError) as e:
        secrets.read_acl(SVC_B)
    assert "SECRET-LOOKING-TEXT" not in str(e.value)


def test_read_acl_refuses_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        secrets.read_acl(SVC_B)


def _app(key: str = SVC_B) -> config.AppConfig:
    return config.AppConfig(
        schema_version=config.SCHEMA_VERSION, slug="example", app_id=1,
        install_id=None, account=None, keychain_key=key,
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_doctor_fails_on_weak_acl(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_run(load("acl_python_and_launcher.txt")))
    assert doctor._check_pem_acl(_app()) == 1
    assert "interpreter" in capsys.readouterr().err


def test_doctor_passes_on_signer_only(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_run(load("acl_signer_only_synthetic.txt")))
    assert doctor._check_pem_acl(_app(SVC_C)) == 0


def test_doctor_fails_closed_when_dump_fails(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_run("", returncode=1))
    assert doctor._check_pem_acl(_app()) == 1


def test_doctor_skips_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert doctor._check_pem_acl(_app()) == 0


def test_register_notice_only_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "Python interpreter" in (secrets.weak_acl_notice() or "")
    monkeypatch.setattr(sys, "platform", "linux")
    assert secrets.weak_acl_notice() is None
