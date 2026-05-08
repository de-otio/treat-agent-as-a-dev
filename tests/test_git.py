from taaad import git


def test_repo_root(tmp_git_repo):
    assert git.repo_root(cwd=tmp_git_repo) == tmp_git_repo


def test_get_set_unset(tmp_git_repo):
    assert git.get_config("bot.app", cwd=tmp_git_repo) is None
    git.set_config("bot.app", "acme-bot", cwd=tmp_git_repo)
    assert git.get_config("bot.app", cwd=tmp_git_repo) == "acme-bot"
    git.unset_config("bot.app", cwd=tmp_git_repo)
    assert git.get_config("bot.app", cwd=tmp_git_repo) is None
    # unset on an absent key is a no-op
    git.unset_config("bot.app", cwd=tmp_git_repo)


def test_remote_helpers(tmp_git_repo):
    url = git.get_remote_url("origin", cwd=tmp_git_repo)
    assert url == "https://github.com/example/repo.git"
    assert git.parse_owner(url) == "example"
    assert git.coerce_https(url) is None  # already HTTPS


def test_coerce_ssh():
    assert (
        git.coerce_https("git@github.com:acme-co/widgets.git")
        == "https://github.com/acme-co/widgets.git"
    )
    assert (
        git.coerce_https("git@github.com:acme-co/widgets")
        == "https://github.com/acme-co/widgets"
    )
    assert git.coerce_https("https://elsewhere.example.com/x/y") is None
    assert git.coerce_https("https://github.com/already/https.git") is None


def test_coerce_ssh_url_form():
    """ssh:// URL form, with and without explicit port."""
    assert (
        git.coerce_https("ssh://git@github.com/acme-co/widgets.git")
        == "https://github.com/acme-co/widgets.git"
    )
    assert (
        git.coerce_https("ssh://git@github.com:22/acme-co/widgets.git")
        == "https://github.com/acme-co/widgets.git"
    )


def test_coerce_ssh_host_alias(monkeypatch):
    """SSH host aliases are resolved via `ssh -G` and rewritten when
    they map to github.com. This is the multi-account convention
    where ~/.ssh/config has e.g. `Host github.com-personal HostName
    github.com IdentityFile ~/.ssh/id_personal`."""
    git._resolve_ssh_hostname.cache_clear()

    def fake_resolve(host: str) -> str:
        mapping = {
            "github.com-personal": "github.com",
            "gh-work": "github.com",
            "github.com": "github.com",
            "elsewhere": "elsewhere.example.com",
        }
        return mapping.get(host, host)

    monkeypatch.setattr(git, "_resolve_ssh_hostname", fake_resolve)
    assert (
        git.coerce_https("git@github.com-personal:acme-co/widgets.git")
        == "https://github.com/acme-co/widgets.git"
    )
    assert (
        git.coerce_https("git@gh-work:acme-co/widgets.git")
        == "https://github.com/acme-co/widgets.git"
    )
    # alias not pointing at github.com → no rewrite
    assert git.coerce_https("git@elsewhere:foo/bar.git") is None
    # unknown alias also degrades cleanly to no-rewrite
    assert git.coerce_https("git@nonexistent-host:foo/bar.git") is None


def test_parse_owner_variants(monkeypatch):
    git._resolve_ssh_hostname.cache_clear()
    monkeypatch.setattr(
        git,
        "_resolve_ssh_hostname",
        lambda h: "github.com" if h in ("github.com", "github.com-personal") else h,
    )
    assert git.parse_owner("https://github.com/acme/widgets.git") == "acme"
    assert git.parse_owner("git@github.com:acme/widgets.git") == "acme"
    assert git.parse_owner("git@github.com-personal:acme/widgets.git") == "acme"
    assert git.parse_owner("ssh://git@github.com/acme/widgets.git") == "acme"
    # not github → None
    assert git.parse_owner("git@elsewhere:foo/bar.git") is None
    # no slash in path → can't extract owner
    assert git.parse_owner("git@github.com:no-slash") is None


def test_resolve_ssh_hostname_no_ssh(monkeypatch):
    """If ssh is not on PATH, `_resolve_ssh_hostname` returns the
    input host unchanged (graceful degrade, not a hard fail)."""
    git._resolve_ssh_hostname.cache_clear()
    monkeypatch.setattr(git.shutil, "which", lambda _name: None)
    assert git._resolve_ssh_hostname("anything") == "anything"
    git._resolve_ssh_hostname.cache_clear()
