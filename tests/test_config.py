"""Loading config.json: a broken file is reported, never replaced with defaults."""

import json

import pytest

from viwoods import config as config_module
from viwoods.config import ConfigError, load_config


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    return path


def test_missing_config_is_created_with_defaults(config_path):
    cfg = load_config()
    assert cfg.token == ""
    assert json.loads(config_path.read_text())["ocr_engine"] == "ollama"


def test_invalid_json_raises_and_leaves_the_file_untouched(config_path):
    original = '{"token": "MY-REAL-TOKEN",}'
    config_path.write_text(original)

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config()

    assert config_path.read_text() == original


def test_single_backslash_windows_path_gets_a_hint(config_path):
    config_path.write_text('{"token": "t", "vault_path": "C:\\Users\\me\\Vault"}')

    with pytest.raises(ConfigError, match="doubled backslashes"):
        load_config()

    assert "C:\\Users\\me\\Vault" in config_path.read_text()


def test_wrongly_typed_setting_raises_and_names_the_field(config_path):
    original = json.dumps({"token": "t", "auto_sync_interval": "often"})
    config_path.write_text(original)

    with pytest.raises(ConfigError, match="auto_sync_interval"):
        load_config()

    assert config_path.read_text() == original


def test_non_object_json_raises(config_path):
    config_path.write_text("[]")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config()


def test_unknown_keys_from_older_versions_are_ignored(config_path):
    config_path.write_text(json.dumps({"token": "t", "mirror_daily": True}))
    assert load_config().token == "t"
