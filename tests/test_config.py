from taaad import config


def _sample(slug="acme-bot", install_id=42):
    return config.AppConfig(
        schema_version=config.SCHEMA_VERSION,
        slug=slug,
        app_id=123,
        install_id=install_id,
        account="acme-co",
        keychain_key=f"github-app-{slug}-pem",
        created_at="2026-05-08T10:00:00+00:00",
    )


def test_round_trip(tmp_config_dir):
    cfg = _sample()
    config.write_app(cfg)
    got = config.read_app(cfg.slug)
    assert got == cfg


def test_list_and_remove(tmp_config_dir):
    config.write_app(_sample("a"))
    config.write_app(_sample("b"))
    slugs = [a.slug for a in config.list_apps()]
    assert slugs == ["a", "b"]
    config.remove_app("a")
    assert [a.slug for a in config.list_apps()] == ["b"]


def test_keychain_key(tmp_config_dir):
    assert config.keychain_key_for("foo") == "github-app-foo-pem"


def test_default_allowlist(tmp_config_dir):
    assert config.allowed_commands() == ["claude", "codex", "aider", "gh", "git"]


def test_allowlist_override(tmp_config_dir):
    config.ensure_dirs()
    p = config.global_config_path()
    p.write_text("[agent]\nallowed_commands = ['echo', 'git']\n")
    assert config.allowed_commands() == ["echo", "git"]


def test_used_by(tmp_config_dir, tmp_path):
    repo = tmp_path / "some-repo"
    repo.mkdir()
    config.record_used_by("acme-bot", repo)
    config.record_used_by("acme-bot", repo)  # idempotent
    paths = config.read_used_by("acme-bot")
    assert paths == [repo.resolve()]
