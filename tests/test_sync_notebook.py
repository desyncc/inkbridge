"""
Notebook sync: a page that fails to download or transcribe must not mark the
note as synced, the vault must exist before anything is written, dated
notebook names must parse to real dates, and a journal entry must stay on
its day instead of following its last edit.
"""

import uuid as uuidlib
from datetime import datetime

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


class VersionedClient(FakeClient):
    """Serves whatever `self.ink` currently holds as every page's image."""

    def __init__(self, ink=b"v1"):
        super().__init__()
        self.ink = ink

    def download_file(self, url, target_path):
        if self.fail_download:
            raise OSError("network down")
        with open(target_path, "wb") as f:
            f.write(self.ink)
        return True


class EchoOCR(FakeOCR):
    """Transcribes a page as the bytes of its image, so edits show up."""

    def transcribe(self, path, context_prompt="", force=False):
        self.calls += 1
        with open(path, "rb") as f:
            return f"ink {f.read().decode()}"


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


# --- Journal dates -----------------------------------------------------------

def ms(day, hour=12):
    """Epoch milliseconds for September `day`, 2026, local time."""
    return int(datetime(2026, 9, day, hour).timestamp() * 1000)


def daily(vault, day):
    return vault.daily_dir / "September" / f"2026-09-{day:02d}.md"


def journal_item(name="Morning thoughts", created=2, modified=5):
    return {"uuid": str(uuidlib.uuid4()), "name": name,
            "createTime": ms(created), "lastModifiedTime": ms(modified)}


@pytest.fixture
def journal_engine(config, vault):
    config.create_missing_daily_notes = True
    return SyncEngine(config, client=FakeClient(), ocr=FakeOCR(), vault=vault)


def test_undated_journal_stays_on_its_first_day_when_edited(journal_engine, vault):
    item = journal_item(created=2, modified=5)
    assert journal_engine.sync_notebook(item, "Paper/Journals")
    assert item["uuid"] in daily(vault, 2).read_text(encoding="utf-8")
    assert not daily(vault, 5).exists()  # creation date, not last-modified

    item["lastModifiedTime"] = ms(7)  # edited on the 7th
    assert journal_engine.sync_notebook(item, "Paper/Journals")

    assert item["uuid"] in daily(vault, 2).read_text(encoding="utf-8")
    assert not daily(vault, 7).exists()
    assert load_sync_state()["notes"][item["uuid"]]["journal_date"] == "2026-09-02"


def test_renaming_a_dated_notebook_moves_its_block(journal_engine, vault):
    item = journal_item(name="2026-09-02")
    journal_engine.sync_notebook(item, "Paper/Journals")
    old = daily(vault, 2)
    old.write_text(old.read_text(encoding="utf-8") + "\nmy own notes\n", encoding="utf-8")

    item.update(name="2026-09-03", lastModifiedTime=ms(8))
    journal_engine.sync_notebook(item, "Paper/Journals")

    assert item["uuid"] in daily(vault, 3).read_text(encoding="utf-8")
    old_text = old.read_text(encoding="utf-8")
    assert item["uuid"] not in old_text
    assert "my own notes" in old_text


def test_copies_left_by_older_versions_are_cleaned_up(journal_engine, vault):
    item = journal_item(created=2, modified=6)
    uuid = item["uuid"]
    heading = vault.config.daily_heading
    # What the old last-modified behavior left behind: a copy on each edit
    # day, one with a tasks callout, one sharing the day with another notebook.
    for day, other in ((5, None), (6, "other-notebook")):
        content = f"## Landing\nuser text {day}\n\n{heading}\n"
        content = vault.inject_journal_section(content, heading, uuid, f"old copy {day}")
        content = vault.inject_tasks_section(content, heading, uuid, ["stale task"])
        if other:
            content = vault.inject_journal_section(content, heading, other, "keep me")
        daily(vault, day).parent.mkdir(parents=True, exist_ok=True)
        daily(vault, day).write_text(content + "\n## Check-ins\n", encoding="utf-8")

    journal_engine.sync_notebook(item, "Paper/Journals")

    assert uuid in daily(vault, 2).read_text(encoding="utf-8")
    for day in (5, 6):
        text = daily(vault, day).read_text(encoding="utf-8")
        assert uuid not in text and "stale task" not in text
        assert f"user text {day}" in text and "## Check-ins" in text
        assert "viwoods:tasks-start" not in text  # emptied tasks region dropped
    assert "keep me" in daily(vault, 6).read_text(encoding="utf-8")


def test_moving_a_notebook_out_of_the_journal_folder_removes_its_block(journal_engine, vault):
    item = journal_item(created=2, modified=5)
    journal_engine.sync_notebook(item, "Paper/Journals")

    item["lastModifiedTime"] = ms(9)
    journal_engine.sync_notebook(item, "Paper/Ideas")

    assert item["uuid"] not in daily(vault, 2).read_text(encoding="utf-8")
    assert "journal_date" not in load_sync_state()["notes"][item["uuid"]]


# --- Edited pages ------------------------------------------------------------

def test_an_edited_page_is_downloaded_and_transcribed_again(config, vault):
    item = new_item()
    client = VersionedClient(b"v1")
    engine = SyncEngine(config, client=client, ocr=EchoOCR(), vault=vault)
    engine.sync_notebook(item, "Paper")

    client.ink = b"v2"  # page 1 edited on the tablet
    item["lastModifiedTime"] = 2000
    assert engine.sync_notebook(item, "Paper") is True

    mirrored = (vault.mirror_dir / "Paper" / "Ideas.md").read_text(encoding="utf-8")
    assert "ink v2" in mirrored and "ink v1" not in mirrored
    attachment = vault.attachments_dir / vault.attachment_filename("Ideas", item["uuid"], 1)
    assert attachment.read_bytes() == b"v2"


def test_a_failed_refresh_keeps_the_old_page_and_retries(config, vault):
    item = new_item()
    client = VersionedClient(b"v1")
    engine = SyncEngine(config, client=client, ocr=EchoOCR(), vault=vault)
    engine.sync_notebook(item, "Paper")

    client.fail_download = True
    item["lastModifiedTime"] = 2000
    assert engine.sync_notebook(item, "Paper") is False

    assert engine.incomplete == ["Ideas"]
    # Still recorded at the old version, so the next sync tries again.
    assert load_sync_state()["notes"][item["uuid"]]["last_modified"] == 1000
    # The previous copy stays in the vault rather than vanishing.
    attachment = vault.attachments_dir / vault.attachment_filename("Ideas", item["uuid"], 1)
    assert attachment.read_bytes() == b"v1"
