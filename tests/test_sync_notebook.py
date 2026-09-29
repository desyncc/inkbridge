"""
Notebook sync: a page that fails to download or transcribe must not mark the
note as synced, the vault must exist before anything is written, and dated
notebook names must parse to real dates.
"""

import uuid as uuidlib

import pytest

from viwoods.ocr import OCRError
from viwoods.sync import SyncEngine, load_sync_state
from viwoods.vault import VaultNotFoundError


class FakeClient:
    def __init__(self, pages=1, fail_download=False):
        self.pages = pages
        self.fail_download = fail_download

    def get_paper_detail(self, uuid, app_type=1):
        return {
            "name": "Ideas",
            "imagePages": [
                {"pageNo": n, "imageUrl": f"https://example.com/p{n}.png", "content": ""}
                for n in range(1, self.pages + 1)
            ],
            "recordings": [],
        }

    def download_file(self, url, target_path):
        if self.fail_download:
            raise OSError("network down")
        with open(target_path, "wb") as f:
            f.write(url.encode())
        return True


class FakeOCR:
    """Returns `results` in order; an exception instance is raised instead."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def transcribe(self, path, context_prompt="", force=False):
        self.calls += 1
        result = self.results.pop(0) if self.results else "text"
        if isinstance(result, Exception):
            raise result
        return result

    def flush_cache(self):
        pass


def new_item():
    # Sync state persists across the test session, so every note needs its own id.
    return {"uuid": str(uuidlib.uuid4()), "name": "Ideas", "lastModifiedTime": 1000}


def recorded(item):
    return item["uuid"] in load_sync_state()["notes"]


def test_successful_sync_is_recorded_and_then_skipped(config, vault):
    item = new_item()
    engine = SyncEngine(config, client=FakeClient(), ocr=FakeOCR("hello"), vault=vault)

    assert engine.sync_notebook(item, "Paper") is True
    assert recorded(item)
    assert engine.sync_notebook(item, "Paper") is False  # unmodified


def test_blank_page_counts_as_synced(config, vault):
    item = new_item()
    engine = SyncEngine(config, client=FakeClient(), ocr=FakeOCR(""), vault=vault)

    assert engine.sync_notebook(item, "Paper") is True
    assert recorded(item)
    assert engine.incomplete == []


def test_ocr_failure_leaves_the_note_unrecorded_so_it_is_retried(config, vault):
    item = new_item()
    ocr = FakeOCR("page one", OCRError("connection refused"))
    engine = SyncEngine(config, client=FakeClient(pages=2), ocr=ocr, vault=vault)

    assert engine.sync_notebook(item, "Paper") is False
    assert not recorded(item)
    assert engine.incomplete == ["Ideas"]

    # The pages that did work are still written to the vault.
    mirrored = (vault.mirror_dir / "Paper" / "Ideas.md").read_text(encoding="utf-8")
    assert "page one" in mirrored

    # Next sync, with the engine back, picks it up despite being unmodified.
    retry = SyncEngine(config, client=FakeClient(pages=2), ocr=FakeOCR("a", "b"), vault=vault)
    assert retry.sync_notebook(item, "Paper") is True
    assert recorded(item)


def test_download_failure_leaves_the_note_unrecorded(config, vault):
    item = new_item()
    engine = SyncEngine(config, client=FakeClient(fail_download=True), ocr=FakeOCR(), vault=vault)

    assert engine.sync_notebook(item, "Paper") is False
    assert not recorded(item)
    assert engine.incomplete == ["Ideas"]


def test_sync_refuses_a_missing_vault(config, tmp_path):
    config.vault_path = str(tmp_path / "does-not-exist")
    engine = SyncEngine(config, client=FakeClient(), ocr=FakeOCR())

    for run in (engine.sync_all, engine.sync_recent_journals, engine.sync_daily_app):
        with pytest.raises(VaultNotFoundError, match="does-not-exist"):
            run()
    with pytest.raises(VaultNotFoundError):
        engine.sync_resource(app_type=1, resource_id="x")

    assert not (tmp_path / "does-not-exist").exists()


def test_placeholder_vault_path_gets_a_specific_message(config, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    config.vault_path = r"C:\Users\You\Obsidian"
    engine = SyncEngine(config, client=FakeClient(), ocr=FakeOCR())

    with pytest.raises(VaultNotFoundError, match="still the placeholder"):
        engine.sync_all()
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize("name, expected", [
    ("2026-09-02", "2026-09-02"),
    ("2026-9-2", "2026-09-02"),
    ("13-04-2026", "2026-04-13"),   # can only be day-first
    ("04-13-2026", "2026-04-13"),   # can only be month-first
    ("04-05-2026", "2026-04-05"),   # ambiguous: month-first by default
    ("2026-13-04", None),
    ("02-30-2026", None),
    ("32-01-2026", None),
    ("Ideas", None),
])
def test_is_date_str(config, name, expected):
    engine = SyncEngine(config, client=FakeClient(), ocr=FakeOCR())
    assert engine.is_date_str(name) == expected


def test_is_date_str_day_first_resolves_ambiguous_dates(config):
    config.day_first = True
    engine = SyncEngine(config, client=FakeClient(), ocr=FakeOCR())
    assert engine.is_date_str("04-05-2026") == "2026-05-04"
    assert engine.is_date_str("04-13-2026") == "2026-04-13"
