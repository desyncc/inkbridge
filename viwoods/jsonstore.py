"""
Concurrency-safe JSON files.

The CLI, the dashboard and the auto-sync scheduler can all be writing the sync
state or the OCR cache at the same time. Each of them used to load the whole
file once and later rewrite it wholesale, so whoever saved last silently
dropped everything the others had done in between.

Every write here takes an exclusive lock on a sidecar `.lock` file, re-reads
the current contents, merges the caller's changes into them, and replaces the
file atomically.
"""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None


@contextmanager
def exclusive_lock(path: Path):
    """Holds an exclusive cross-process lock for `path` via `<path>.lock`."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "a+") as handle:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Reads a JSON object, returning a copy of `default` if it is missing or corrupt."""
    fallback = dict(default or {})
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else fallback
    except (OSError, ValueError):
        return fallback


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Writes JSON via a temp file in the same directory, then renames it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def update_json(
    path: Path,
    mutate: Callable[[Dict[str, Any]], None],
    default: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Applies `mutate` to the file's current contents under an exclusive lock and
    writes the result back. Returns the merged data.
    """
    with exclusive_lock(path):
        data = read_json(path, default)
        mutate(data)
        write_json_atomic(path, data)
        return data
