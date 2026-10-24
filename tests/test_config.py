from pathlib import Path

import pytest

from paperdb import config
from paperdb.config import Config, ConfigError


@pytest.fixture
def settings(tmp_path) -> Config:
    return Config(papers_dir=tmp_path / "Papers", database_id="db-123", email="me@example.com")


def test_save_then_load_round_trips(tmp_path, settings):
    path = config.save(settings, tmp_path / "config.toml")
    loaded = config.load(path)
    assert loaded.papers_dir == settings.papers_dir
    assert loaded.database_id == "db-123"
    assert loaded.email == "me@example.com"


def test_the_token_is_never_written_to_the_file(tmp_path, settings, monkeypatch):
    monkeypatch.setenv(config.TOKEN_ENV, "secret-token-value")
    path = config.save(settings, tmp_path / "config.toml")
    assert "secret-token-value" not in path.read_text()


def test_a_windows_style_path_survives_quoting(tmp_path):
    awkward = Config(papers_dir=Path(r"/tmp/a\b/c d"), database_id="x")
    path = config.save(awkward, tmp_path / "config.toml")
    assert config.load(path).papers_dir == awkward.papers_dir


def test_load_without_a_config_says_to_run_init(tmp_path):
    with pytest.raises(ConfigError, match="paperdb init"):
        config.load(tmp_path / "missing.toml")


def test_load_without_papers_dir_says_to_run_init_again(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('database_id = "db"\n')
    with pytest.raises(ConfigError, match="init"):
        config.load(path)


def test_token_explains_where_to_get_one(monkeypatch):
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    with pytest.raises(ConfigError, match="my-integrations"):
        config.token()


def test_bib_lands_beside_the_pdfs(settings):
    assert settings.bib_path == settings.papers_dir / "library.bib"
