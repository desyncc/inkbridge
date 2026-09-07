"""
Daily app sync: pulls the Viwoods 'Daily' app's pages (type=1) and to-dos
(type=2) - a separate cloud resource from the Paper/Meeting/... folder tree -
into the matching daily note.
"""

from viwoods.sync import SyncEngine
from viwoods.vault import VIWOODS_START

from conftest import DAILY_HEADING as HEADING


class FakeClient:
    def __init__(self, notes=None, todos=None):
        self._notes = notes or []
        self._todos = todos or []

    def get_daily_list(self, start_date, end_date, app_type=1):
        return self._notes if app_type == 1 else self._todos

    def download_file(self, url, target_path):
        with open(target_path, "wb") as f:
            f.write(b"fake-png")
        return True


class FakeOCR:
    def __init__(self):
        self.calls = 0

    def transcribe(self, path, context_prompt="", force=False):
        self.calls += 1
        return "ocr text"

    def flush_cache(self):
        pass


def note_path(vault, date_str, month="September"):
    return vault.daily_dir / month / f"{date_str}.md"


def write_note(vault, date_str, month="September"):
    path = note_path(vault, date_str, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{HEADING}\n", encoding="utf-8")
    return path


def make_note_item(id_, date, url="", page_order=1, last_mod=1000, **overrides):
    item = {
        "id": id_, "date": date, "type": 1, "pageOrder": page_order,
        "content": "", "url": url, "isDelete": 0, "isFinish": 0,
        "lastModifiedTime": last_mod, "level": 0,
    }
    item.update(overrides)
    return item


def make_todo_item(id_, date, content, level=0, is_finish=0, last_mod=1000):
    return {
        "id": id_, "date": date, "type": 2, "pageOrder": 0,
        "content": content, "url": "", "isDelete": 0, "isFinish": is_finish,
        "lastModifiedTime": last_mod, "level": level, "createdAt": f"2026-09-{id_:02d}",
    }


def test_syncs_a_page_and_todos_into_the_daily_note(config, vault):
    date_str = "2026-09-05"
    write_note(vault, date_str)

    notes = [make_note_item(1, date_str, url="https://example.com/p.png")]
    todos = [
        make_todo_item(2, date_str, "Buy milk", level=0),
        make_todo_item(3, date_str, "Sub item", level=100, is_finish=1),
    ]
    ocr = FakeOCR()
    engine = SyncEngine(config, client=FakeClient(notes=notes, todos=todos), ocr=ocr, vault=vault)

    synced = engine.sync_daily_app(days_back=30)

    assert synced == 1
    assert ocr.calls == 1
    text = note_path(vault, date_str).read_text(encoding="utf-8")
    assert VIWOODS_START in text
    assert "<!-- viwoods:note daily-app -->" in text
    assert "ocr text" in text
    assert "- [ ] Buy milk" in text
    assert "  - [x] Sub item" in text


def test_deleted_items_are_filtered_out(config, vault):
    date_str = "2026-09-05"
    write_note(vault, date_str)

    notes = [make_note_item(1, date_str, isDelete=1)]
    engine = SyncEngine(config, client=FakeClient(notes=notes), ocr=FakeOCR(), vault=vault)

    assert engine.sync_daily_app(days_back=30) == 0
    assert "viwoods:note daily-app" not in note_path(vault, date_str).read_text(encoding="utf-8")


def test_unmodified_entry_is_skipped_on_a_second_sync(config, vault):
    # A date not touched by any other test: the sync-state file lives under
    # the session-wide VIWOODS_DATA_DIR (see conftest), so it persists across
    # tests and a reused date would collide with another test's cached state.
    date_str = "2026-09-10"
    write_note(vault, date_str)

    todos = [make_todo_item(2, date_str, "Buy milk", last_mod=1000)]
    engine = SyncEngine(config, client=FakeClient(todos=todos), ocr=FakeOCR(), vault=vault)

    assert engine.sync_daily_app(days_back=30) == 1
    assert engine.sync_daily_app(days_back=30) == 0


def test_force_resyncs_even_when_unmodified(config, vault):
    date_str = "2026-09-11"
    write_note(vault, date_str)

    todos = [make_todo_item(2, date_str, "Buy milk", last_mod=1000)]
    engine = SyncEngine(config, client=FakeClient(todos=todos), ocr=FakeOCR(), vault=vault)

    assert engine.sync_daily_app(days_back=30) == 1
    assert engine.sync_daily_app(days_back=30, force=True) == 1


def test_create_missing_daily_notes_false_skips_absent_dates(config, vault):
    config.create_missing_daily_notes = False
    date_str = "2026-09-12"

    todos = [make_todo_item(2, date_str, "Buy milk")]
    engine = SyncEngine(config, client=FakeClient(todos=todos), ocr=FakeOCR(), vault=vault)

    assert engine.sync_daily_app(days_back=30) == 0
    assert not note_path(vault, date_str).exists()


def test_two_dates_are_kept_independent(config, vault):
    write_note(vault, "2026-09-13")
    write_note(vault, "2026-09-14")

    todos = [
        make_todo_item(1, "2026-09-13", "Task A"),
        make_todo_item(2, "2026-09-14", "Task B"),
    ]
    engine = SyncEngine(config, client=FakeClient(todos=todos), ocr=FakeOCR(), vault=vault)

    assert engine.sync_daily_app(days_back=30) == 2
    a = note_path(vault, "2026-09-13").read_text(encoding="utf-8")
    b = note_path(vault, "2026-09-14").read_text(encoding="utf-8")
    assert "Task A" in a and "Task B" not in a
    assert "Task B" in b and "Task A" not in b
