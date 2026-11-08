"""Parse and judge a macOS keychain item's access list.

Pure functions only: no subprocess, no filesystem. The shell part
(`secrets.dump_keychain_acl`) feeds `security dump-keychain -a` output
in. That command lists attributes and access lists but never secret
values, and never prompts.

Why this exists: the keychain decides who may read an item by the
*executable* that asks, so an item stored by Python trusts the Python
interpreter, and every script that interpreter runs. `verdict` is an
allowlist: anything on the trusted-application list that is not the
signed taaad helper fails, whatever it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Authorizations that let a trusted application read key material.
# `encrypt` and `integrity` entries are not reads and are ignored.
_READ_AUTHS = frozenset({"decrypt", "export_clear", "export_wrapped", "derive", "sign", "mac", "any"})

# Partition IDs under which Apple-signed binaries (including
# /usr/bin/security) may read the item without a prompt.
_APPLE_PARTITIONS = frozenset({"apple:", "apple-tool:"})

# Identity the signed helper must carry before it may hold a PEM.
# Developer ID team and code-signing identifier (plan 0003, D1). The
# identifier is fixed for good: changing it orphans every key item.
SIGNER_TEAM_ID = "U26DY5A9DU"
SIGNER_IDENTIFIER = "org.de-otio.taaad-signer"
SIGNER_PARTITION = f"teamid:{SIGNER_TEAM_ID}"

# The helper's designated requirement, as `codesign -dr -` and
# `security dump-keychain -a` print it (the standard Developer ID form).
SIGNER_REQUIREMENT = (
    f'identifier "{SIGNER_IDENTIFIER}" and anchor apple generic'
    " and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */"
    " and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */"
    f" and certificate leaf[subject.OU] = {SIGNER_TEAM_ID}"
)

# The same requirement in source form for `codesign --verify -R=`: the
# `/* exists */` comments are print-only and not part of the syntax.
SIGNER_CSREQ = " ".join(re.sub(r"/\*.*?\*/", " ", SIGNER_REQUIREMENT).split())

_INTERPRETERS = ("python", "ruby", "node", "perl", "php", "java", "bash", "zsh", "sh", "osascript")


@dataclass(frozen=True)
class TrustedApp:
    path: str
    requirement: str  # "none" when the item names a path with no code requirement


@dataclass(frozen=True)
class AclReport:
    service: str
    account: str
    # Applications trusted for read access, in dump order.
    apps: tuple[TrustedApp, ...]
    # True when a read-granting entry says `applications: <null>`: any app may read.
    any_app: bool
    partitions: frozenset[str]


@dataclass(frozen=True)
class Finding:
    subject: str
    reason: str


_ATTR = r'\s+"{name}"<[a-z0-9]+>=(?:<NULL>|"(?P<v>[^"]*)")'


def _attr(block: str, name: str) -> str | None:
    m = re.search(_ATTR.format(name=name), block)
    return m.group("v") if m and m.group("v") is not None else None


def _split_items(dump: str) -> list[str]:
    parts = re.split(r'(?m)^(?=keychain: ")', dump)
    return [p for p in parts if p.startswith("keychain: ")]


def _split_entries(block: str) -> list[str]:
    parts = re.split(r"(?m)^(?=    entry \d+:)", block)
    return [p for p in parts if p.lstrip().startswith("entry ")]


_APP_LINE = re.compile(r"^\s+\d+: (?P<path>.*) \([^()]*\)$")
_REQ_LINE = re.compile(r"^\s+requirement: (?P<req>.*)$")


def _parse_entry(entry: str) -> tuple[frozenset[str], bool, tuple[TrustedApp, ...], frozenset[str]]:
    auths: frozenset[str] = frozenset()
    m = re.search(r"authorizations \(\d+\): (?P<a>.*)", entry)
    if m:
        auths = frozenset(m.group("a").replace(",", " ").split())

    any_app = bool(re.search(r"(?m)^\s+applications: <null>\s*$", entry))

    apps: list[TrustedApp] = []
    path: str | None = None
    for line in entry.splitlines():
        am = _APP_LINE.match(line)
        if am:
            if path is not None:
                apps.append(TrustedApp(path, "none"))
            path = am.group("path")
            continue
        rm = _REQ_LINE.match(line)
        if rm and path is not None:
            apps.append(TrustedApp(path, rm.group("req").strip()))
            path = None
    if path is not None:
        apps.append(TrustedApp(path, "none"))

    partitions: frozenset[str] = frozenset()
    if "partition_id" in auths:
        dm = re.search(r"description: (?P<d>.*)", entry)
        if dm:
            partitions = frozenset(p.strip() for p in dm.group("d").split(",") if p.strip())
    return auths, any_app, tuple(apps), partitions


def parse_acl(dump: str, service: str) -> list[AclReport]:
    """One report per keychain item whose service attribute equals `service`.

    Empty list when the item is absent. Several items can share a
    service (different accounts), hence a list.
    """
    reports: list[AclReport] = []
    for block in _split_items(dump):
        if _attr(block, "svce") != service:
            continue
        apps: list[TrustedApp] = []
        any_app = False
        partitions: frozenset[str] = frozenset()
        for entry in _split_entries(block):
            auths, e_any, e_apps, e_parts = _parse_entry(entry)
            partitions |= e_parts
            if auths & _READ_AUTHS:
                any_app = any_app or e_any
                apps.extend(a for a in e_apps if a not in apps)
        reports.append(
            AclReport(
                service=service,
                account=_attr(block, "acct") or "",
                apps=tuple(apps),
                any_app=any_app,
                partitions=partitions,
            )
        )
    return reports


def _canonical_requirement(req: str) -> str:
    """Drop `/* ... */` comments and quotes, collapse whitespace.

    Tools disagree on quoting (`codesign` leaves the Team ID bare,
    other printers quote it), so compare in a form both reduce to.
    """
    req = re.sub(r"/\*.*?\*/", " ", req).replace('"', "")
    return " ".join(req.split())


def is_signer(app: TrustedApp) -> bool:
    """True only for an app whose code requirement is exactly the helper's.

    Exact match, not substring: `identifier "<helper>" or anchor apple`
    contains every expected clause and admits all Apple code. A path
    alone (`requirement: none`) never qualifies either: the keychain
    would then trust whatever sits at that path.
    """
    return _canonical_requirement(app.requirement) == _canonical_requirement(SIGNER_REQUIREMENT)


def _describe(app: TrustedApp) -> str:
    base = app.path.rsplit("/", 1)[-1].lower()
    if app.path == "/usr/bin/security":
        return "/usr/bin/security reads this item without a prompt, so any shell command can"
    if base.startswith(_INTERPRETERS):
        return f"interpreter {app.path}: any script it runs can read the item"
    if app.requirement == "none":
        return f"{app.path} is trusted by path only (no code requirement)"
    return f"{app.path} is not the signed taaad helper"


def verdict(reports: list[AclReport]) -> list[Finding]:
    """Empty list means the item is acceptable. An absent item is a finding."""
    if not reports:
        return [Finding("item", "no keychain item found for this service")]
    findings: list[Finding] = []
    for r in reports:
        subject = f"{r.service} (account {r.account})" if r.account else r.service
        if r.any_app:
            findings.append(Finding(subject, "any application may read the item"))
        for app in r.apps:
            if not is_signer(app):
                findings.append(Finding(subject, _describe(app)))
        apple = sorted(r.partitions & _APPLE_PARTITIONS)
        if apple:
            findings.append(
                Finding(subject, f"partition list admits Apple-signed tools ({', '.join(apple)})")
            )
        other = sorted(r.partitions - _APPLE_PARTITIONS - {SIGNER_PARTITION})
        if other:
            findings.append(
                Finding(subject, f"partition list admits code outside the taaad team ({', '.join(other)})")
            )
        if not r.apps and not r.any_app:
            findings.append(Finding(subject, "no signed taaad helper is trusted for this item"))
    return findings
