"""A fake `taaad-signer`: a generated executable that implements the CLI
contract of signer/SPEC.md section 4 from a JSON scenario.

The scenario path and call-log path are baked into the script, because
the real helper (and so the fake) receives no environment. The scenario
maps a command name to {"exit": int, "stdout": obj|str, "stderr": obj|str,
"sleep": seconds}. Every call is appended to the log.
"""

from __future__ import annotations

import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

TOKEN = "ghs_SECRETTOKENVALUE0123456789"

_SCRIPT = '''#!{python}
import json, os, sys, time
argv = sys.argv[1:]
stdin = sys.stdin.read() if argv and argv[0] == "import" else ""
with open({log!r}, "a") as f:
    f.write(json.dumps({{"argv": argv, "stdin": stdin, "env": dict(os.environ)}}) + "\\n")
with open({scenario!r}) as f:
    scen = json.load(f)
r = scen.get(argv[0] if argv else "", {{"exit": 1, "stderr": {{"error": "internal", "message": "no scenario"}}}})
time.sleep(r.get("sleep", 0))
def emit(stream, v):
    if v is None:
        return
    stream.write(v if isinstance(v, str) else json.dumps(v, separators=(",", ":")))
    stream.write("\\n")
emit(sys.stdout, r.get("stdout"))
emit(sys.stderr, r.get("stderr"))
sys.exit(r.get("exit", 0))
'''


@dataclass(frozen=True)
class FakeSigner:
    path: Path  # the executable
    app: Path  # a bundle-shaped directory around it
    scenario_file: Path
    log_file: Path

    def set(self, command: str, *, exit: int = 0, stdout=None, stderr=None, sleep: float = 0) -> None:
        scen = json.loads(self.scenario_file.read_text())
        scen[command] = {"exit": exit, "stdout": stdout, "stderr": stderr, "sleep": sleep}
        self.scenario_file.write_text(json.dumps(scen))

    def calls(self) -> list[dict]:
        if not self.log_file.exists():
            return []
        return [json.loads(ln) for ln in self.log_file.read_text().splitlines() if ln]

    def commands(self) -> list[str]:
        return [c["argv"][0] for c in self.calls()]


def make(root: Path) -> FakeSigner:
    app = root / "taaad-signer.app"
    exe = app / "Contents" / "MacOS" / "taaad-signer"
    exe.parent.mkdir(parents=True)
    scenario = root / "scenario.json"
    log = root / "calls.jsonl"
    scenario.write_text("{}")
    exe.write_text(_SCRIPT.format(python=sys.executable, log=str(log), scenario=str(scenario)))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return FakeSigner(exe, app, scenario, log)


def mint_output(**over) -> dict:
    out = {
        "token": TOKEN,
        "expires_at": "2030-01-01T00:00:00Z",
        "permissions": {},
        "repository_selection": "all",
        "repositories": [],
        "cached": False,
        "account": "example-org",
    }
    out.update(over)
    return out


import pytest  # noqa: E402


@pytest.fixture
def fake_signer(tmp_path, monkeypatch) -> FakeSigner:
    """A fake helper installed at a temporary SIGNER_PATH, on 'macOS'."""
    from taaad import signer

    fake = make(tmp_path / "signer")
    monkeypatch.setattr(signer, "SIGNER_PATH", fake.path)
    monkeypatch.setattr(signer, "SIGNER_APP", fake.app)
    # A test cannot create root-owned files; path_problem has its own tests.
    monkeypatch.setattr(signer, "PATH_CHECK", lambda _p: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    return fake
