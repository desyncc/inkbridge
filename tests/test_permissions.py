"""
The data directory holds the Viwoods token, the Gemini key and scans of
handwriting, so what InkBridge creates is private to the user.
"""

import os
import stat

import pytest

import viwoods.config as config_module
import viwoods.paths as paths
from viwoods.config import Config, load_config, save_config
from viwoods.jsonstore import update_json, write_json_atomic

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_json_files_are_written_private(tmp_path):
    target = tmp_path / "state.json"
    write_json_atomic(target, {"a": 1})
    assert mode(target) == 0o600

    update_json(target, lambda d: d.update(b=2))
    assert mode(target) == 0o600


def test_a_config_from_an_older_version_is_tightened_when_loaded(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_config_to(path, Config(token="secret"))
    os.chmod(path, 0o644)
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)

    load_config()

    assert mode(path) == 0o600


def save_config_to(path, cfg):
    write_json_atomic(path, cfg.model_dump())


def test_saving_the_config_keeps_it_private(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    save_config(Config(token="secret"))
    assert mode(path) == 0o600


def test_the_default_data_directory_is_created_private(tmp_path, monkeypatch):
    default = tmp_path / ".viwoods"
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(paths, "DEFAULT_DATA_DIR", default)

    assert paths.data_dir() == default
    assert mode(default) == 0o700


def test_an_existing_default_data_directory_is_tightened(tmp_path, monkeypatch):
    default = tmp_path / ".viwoods"
    default.mkdir()
    os.chmod(default, 0o755)
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(paths, "DEFAULT_DATA_DIR", default)

    paths.data_dir()

    assert mode(default) == 0o700


def test_a_new_custom_data_directory_is_created_private(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "data"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(custom))

    paths.data_dir()

    assert mode(custom) == 0o700


def test_an_existing_custom_data_directory_is_left_alone(tmp_path, monkeypatch):
    custom = tmp_path / "shared"
    custom.mkdir()
    os.chmod(custom, 0o755)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(custom))

    paths.data_dir()

    assert mode(custom) == 0o755
