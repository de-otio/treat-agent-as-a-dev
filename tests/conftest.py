from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAAAD_CONFIG_DIR", str(tmp_path / "taaad"))
    return tmp_path / "taaad"


@pytest.fixture
def tmp_git_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin",
         "https://github.com/example/repo.git"],
        cwd=repo, check=True,
    )
    return repo


@pytest.fixture(autouse=True)
def _isolate_keyring(monkeypatch):
    """Force the in-memory keyring for the test suite so tests don't
    touch the real macOS Keychain / Linux Secret Service / Windows
    Credential Manager."""
    import keyring
    import keyring.backends.fail
    import keyring.backend

    class InMemory(keyring.backend.KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self):
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service, username):
            return self._store.get((service, username))

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def delete_password(self, service, username):
            self._store.pop((service, username), None)

    keyring.set_keyring(InMemory())
    yield
