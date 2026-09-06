"""
Shared test fixtures.

VIWOODS_DATA_DIR is set before viwoods is imported for the first time, because
the module-level paths (config, sync state, OCR cache) are resolved at import
time. This keeps a test run away from the real ~/.viwoods.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["VIWOODS_DATA_DIR"] = tempfile.mkdtemp(prefix="viwoods-tests-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from viwoods.config import Config  # noqa: E402
from viwoods.vault import ObsidianVault  # noqa: E402

DAILY_HEADING = "# Transcribed text from AiPaper:"


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        token="test-token",
        vault_path=str(tmp_path / "vault"),
        daily_heading=DAILY_HEADING,
        ocr_engine="ollama",
    )


@pytest.fixture
def vault(config) -> ObsidianVault:
    return ObsidianVault(config)
