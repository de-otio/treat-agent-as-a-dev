from taaad import config, git
from taaad.commands import init, uninit


def _register(slug, install_id=42, account="example"):
    config.write_app(
        config.AppConfig(
            schema_version=config.SCHEMA_VERSION,
            slug=slug,
            app_id=999,
            install_id=install_id,
            account=account,
            keychain_key=f"github-app-{slug}-pem",
            created_at="2026-05-08T10:00:00+00:00",
        )
    )


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_init_writes_local_config(tmp_config_dir, tmp_git_repo, monkeypatch):
    _register("example-bot", account="example")
    monkeypatch.chdir(tmp_git_repo)
    rc = init.run(_Args(app="example-bot"))
    assert rc == 0
    assert git.get_config("bot.app", cwd=tmp_git_repo) == "example-bot"
    assert git.get_config("user.name", cwd=tmp_git_repo) == "example-bot[bot]"
    email = git.get_config("user.email", cwd=tmp_git_repo)
    assert email and email.endswith("+example-bot[bot]@users.noreply.github.com")
    helper = git.get_config("credential.helper", cwd=tmp_git_repo)
    assert helper and "credential-helper example-bot" in helper


def test_uninit_round_trip(tmp_config_dir, tmp_git_repo, monkeypatch):
    _register("example-bot", account="example")
    monkeypatch.chdir(tmp_git_repo)
    init.run(_Args(app="example-bot"))
    uninit.run(_Args(force=False))
    assert git.get_config("bot.app", cwd=tmp_git_repo) is None
    assert git.get_config("user.name", cwd=tmp_git_repo) is None
    assert git.get_config("user.email", cwd=tmp_git_repo) is None
    assert git.get_config("credential.helper", cwd=tmp_git_repo) is None


def test_uninit_keeps_unfamiliar_helper(tmp_config_dir, tmp_git_repo, monkeypatch):
    _register("example-bot", account="example")
    monkeypatch.chdir(tmp_git_repo)
    init.run(_Args(app="example-bot"))
    # User changed it manually after init
    git.set_config("credential.helper", "store", cwd=tmp_git_repo)
    uninit.run(_Args(force=False))
    # Unfamiliar helper kept; bot.app cleared
    assert git.get_config("credential.helper", cwd=tmp_git_repo) == "store"
    assert git.get_config("bot.app", cwd=tmp_git_repo) is None
