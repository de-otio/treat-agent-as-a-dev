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


def test_resolve_ssh_hostname_no_config(monkeypatch, tmp_path):
    """If neither config file exists, `_resolve_ssh_hostname` returns
    the input host unchanged (graceful degrade, not a hard fail)."""
    git._resolve_ssh_hostname.cache_clear()
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", tmp_path / "no-such-file")
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "no-such-system")
    assert git._resolve_ssh_hostname("anything") == "anything"
    git._resolve_ssh_hostname.cache_clear()


def _write_config(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_ssh_config_literal_match(monkeypatch, tmp_path):
    """Plain `Host alias HostName foo` is the common multi-account
    pattern. Verify literal match resolution."""
    git._resolve_ssh_hostname.cache_clear()
    cfg = tmp_path / "config"
    _write_config(cfg, """
Host github.com-personal
  HostName github.com
  IdentityFile ~/.ssh/id_personal
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", cfg)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    assert git._resolve_ssh_hostname("github.com-personal") == "github.com"
    assert git._resolve_ssh_hostname("github.com") == "github.com"
    assert git._resolve_ssh_hostname("unrelated") == "unrelated"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_glob_and_negation(monkeypatch, tmp_path):
    """OpenSSH supports glob patterns and `!`-prefix negation in
    `Host` blocks; verify both."""
    git._resolve_ssh_hostname.cache_clear()
    cfg = tmp_path / "config"
    _write_config(cfg, """
Host !github.com-test github.com-*
  HostName github.com
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", cfg)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    assert git._resolve_ssh_hostname("github.com-personal") == "github.com"
    assert git._resolve_ssh_hostname("github.com-work") == "github.com"
    # negated explicitly → falls through, returns input unchanged
    assert git._resolve_ssh_hostname("github.com-test") == "github.com-test"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_first_match_wins(monkeypatch, tmp_path):
    """OpenSSH applies the *first* HostName directive from any
    matching block, not the last. Verify our walker mirrors that."""
    git._resolve_ssh_hostname.cache_clear()
    cfg = tmp_path / "config"
    _write_config(cfg, """
Host *
  HostName fallback.example.com

Host github.com-personal
  HostName github.com
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", cfg)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    # `Host *` matches first and provides a HostName → that wins
    assert git._resolve_ssh_hostname("github.com-personal") == "fallback.example.com"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_match_block_skipped(monkeypatch, tmp_path):
    """**Security**: `Match` blocks must be skipped entirely. A
    `HostName` directive inside a `Match` (even `Match host`) must
    not be returned, because the same parser path also reaches
    `Match exec` — and we never want to be a trigger for that."""
    git._resolve_ssh_hostname.cache_clear()
    cfg = tmp_path / "config"
    _write_config(cfg, """
Match host github.com-personal
  HostName should-not-be-returned

Host github.com-personal
  HostName github.com
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", cfg)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    # The Match block is skipped; the subsequent Host block wins.
    assert git._resolve_ssh_hostname("github.com-personal") == "github.com"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_match_only_returns_input(monkeypatch, tmp_path):
    """If the *only* alias resolution lives in a `Match` block, we
    deliberately don't find it. Document this behaviour explicitly:
    users who rely on `Match host` for aliasing must switch to plain
    `Host` blocks for taaad's rewrite to find them."""
    git._resolve_ssh_hostname.cache_clear()
    cfg = tmp_path / "config"
    _write_config(cfg, """
Match host github.com-personal
  HostName github.com
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", cfg)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    assert git._resolve_ssh_hostname("github.com-personal") == "github.com-personal"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_include(monkeypatch, tmp_path):
    """`Include` directives recurse into other config files."""
    git._resolve_ssh_hostname.cache_clear()
    main = tmp_path / "config"
    sub = tmp_path / "config.d" / "personal"
    _write_config(sub, """
Host github.com-personal
  HostName github.com
""")
    _write_config(main, f"""
Include {sub}
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", main)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    assert git._resolve_ssh_hostname("github.com-personal") == "github.com"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_include_cycle(monkeypatch, tmp_path):
    """Include cycles must not infinite-loop or error out."""
    git._resolve_ssh_hostname.cache_clear()
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_config(a, f"Include {b}\nHost target\n  HostName real.example.com\n")
    _write_config(b, f"Include {a}\n")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", a)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    assert git._resolve_ssh_hostname("target") == "real.example.com"
    git._resolve_ssh_hostname.cache_clear()


def test_ssh_config_quoted_value(monkeypatch, tmp_path):
    """OpenSSH accepts quoted values; verify dequoting."""
    git._resolve_ssh_hostname.cache_clear()
    cfg = tmp_path / "config"
    _write_config(cfg, """
Host alias
  HostName "github.com"
""")
    monkeypatch.setattr(git, "_SSH_USER_CONFIG", cfg)
    monkeypatch.setattr(git, "_SSH_SYSTEM_CONFIG", tmp_path / "missing")
    assert git._resolve_ssh_hostname("alias") == "github.com"
    git._resolve_ssh_hostname.cache_clear()
