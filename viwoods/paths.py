"""
Where InkBridge keeps its data.

Config, sync state, the OCR cache and downloaded page scans used to live in the
project directory, which breaks as soon as the code sits somewhere read-only,
shared, or under version control (the config holds a JWT). They now live in a
per-user data directory:

    $VIWOODS_DATA_DIR   if set
    ~/.viwoods          otherwise

Files left over from the old layout are moved across on first use.
"""

import os
import shutil
from pathlib import Path
from typing import Optional

DATA_DIR_ENV = "VIWOODS_DATA_DIR"
DEFAULT_DATA_DIR = Path.home() / ".viwoods"

# Old location (the project root) -> new name inside the data directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LEGACY_FILES = {
    ".viwoods_config.json": "config.json",
    ".viwoods_sync_state.json": "sync_state.json",
    ".viwoods_ocr_cache.json": "ocr_cache.json",
    ".viwoods_cache": "cache",
}

_migrated = False


def _migrate_legacy_files(target: Path) -> None:
    """Moves data from the old project-root layout, once per process."""
    global _migrated
    if _migrated:
        return
    _migrated = True

    if target.resolve() == _PROJECT_ROOT.resolve():
        return

    for legacy_name, new_name in _LEGACY_FILES.items():
        source = _PROJECT_ROOT / legacy_name
        destination = target / new_name
        if not source.exists() or destination.exists():
            continue
        try:
            shutil.move(str(source), str(destination))
            print(f"Moved {source} -> {destination}")
        except OSError as e:
            print(f"Warning: could not move {source} to {destination}: {e}")


def data_dir(create: bool = True) -> Path:
    """Returns the per-user data directory, migrating any legacy files."""
    override: Optional[str] = os.environ.get(DATA_DIR_ENV)
    base = Path(override).expanduser() if override else DEFAULT_DATA_DIR

    if create:
        base.mkdir(parents=True, exist_ok=True)
        # Only the default location adopts legacy files: an explicit
        # VIWOODS_DATA_DIR is a deliberate choice (and a test run) that should
        # never quietly relocate someone's existing data.
        if override is None:
            _migrate_legacy_files(base)

    return base


def data_path(name: str) -> Path:
    """Path to one file or directory inside the data directory."""
    return data_dir() / name
