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
