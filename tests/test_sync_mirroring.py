"""
The vault mirrors the device's folder tree: a notebook that is deleted, moved
or renamed on the tablet is deleted, moved or renamed in the vault too, and a
new notebook lands in the folder it was created in.
"""

import uuid as uuidlib

import pytest

import viwoods.sync as sync_module
from viwoods.sync import SyncEngine, load_sync_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """A fresh sync state per test: pruning looks at every tracked notebook."""
    monkeypatch.setattr(sync_module, "STATE_FILE", tmp_path / "sync_state.json")
    monkeypatch.setattr(sync_module, "LOCAL_CACHE_DIR", tmp_path / "cache")


class TreeClient:
    """A device: `tree` maps a folder's resource id ("" = root) to its items."""

    def __init__(self, tree, count=None, app_type=1, pages=1):
        self.tree = tree
        self.count = count
        self.app_type = app_type
        self.pages = pages
        self.fail = False

    def get_devices(self):
        return []

    def get_root_folders(self):
        total = sum(len(v) for v in self.tree.values()) if self.count is None else self.count
        return [{"name": "Paper", "appType": self.app_type, "count": total}]

    def get_folder_items(self, app_type=1, resource_id="", sub_tab=-1):
        if self.fail:
            raise RuntimeError("network down")
        return list(self.tree.get(resource_id, []))

    def get_daily_list(self, start, end, app_type=1):
        return []

    def get_paper_detail(self, uuid, app_type=1):
        return {
            "imagePages": [
                {"pageNo": n, "imageUrl": f"https://example.com/{uuid}/p{n}.png", "content": ""}
                for n in range(1, self.pages + 1)
            ],
            "recordings": [],
        }

    def download_file(self, url, target_path):
        with open(target_path, "wb") as f:
            f.write(url.encode())
        return True


class QuietOCR:
    def transcribe(self, path, context_prompt="", force=False):
        return "some text"

    def flush_cache(self):
        pass


def folder(name, uuid=None):
    return {"uuid": uuid or str(uuidlib.uuid4()), "name": name, "resourceType": 0}


def notebook(name, uuid=None, modified=1000):
    return {"uuid": uuid or str(uuidlib.uuid4()), "name": name, "lastModifiedTime": modified}


def engine_for(config, vault, client):
    return SyncEngine(config, client=client, ocr=QuietOCR(), vault=vault)


def mirrored(vault, *parts):
    return vault.mirror_dir.joinpath("Paper", *parts)


def attachments(vault, uuid):
    if not vault.attachments_dir.is_dir():
        return []
    return sorted(p.name for p in vault.attachments_dir.iterdir() if f"_{uuid[:8]}_" in p.name)


def test_new_notebook_lands_in_its_folder(config, vault):
    health = folder("Health")
    note = notebook("Blood pressure")
    client = TreeClient({"": [health], health["uuid"]: [note]})

    engine_for(config, vault, client).sync_all()

    assert mirrored(vault, "Health", "Blood pressure.md").is_file()


def test_deleting_a_notebook_removes_everything_it_left_in_the_vault(config, vault):
    health = folder("Health")
    keep, gone = notebook("Keep"), notebook("Blood pressure")
    client = TreeClient({"": [health], health["uuid"]: [keep, gone]})
    engine_for(config, vault, client).sync_all()
    assert attachments(vault, gone["uuid"])

    client.tree[health["uuid"]] = [keep]
    result = engine_for(config, vault, client).sync_all()

    assert not mirrored(vault, "Health", "Blood pressure.md").exists()
    assert mirrored(vault, "Health", "Keep.md").is_file()
    assert attachments(vault, gone["uuid"]) == []
    assert attachments(vault, keep["uuid"])
    assert gone["uuid"] not in load_sync_state()["notes"]
    assert keep["uuid"] in load_sync_state()["notes"]
    assert [r["name"] for r in result["removed"]] == ["Blood pressure"]


def test_deleting_the_last_notebook_in_a_folder_removes_the_folder(config, vault):
    health, work = folder("Health"), folder("Work")
    gone, other = notebook("Blood pressure"), notebook("Standup")
    client = TreeClient({"": [health, work], health["uuid"]: [gone], work["uuid"]: [other]})
    engine_for(config, vault, client).sync_all()

    client.tree = {"": [work], work["uuid"]: [other]}
    engine_for(config, vault, client).sync_all()

    assert not mirrored(vault, "Health").exists()
    assert mirrored(vault, "Work", "Standup.md").is_file()
    assert vault.mirror_dir.is_dir()


def test_deleting_a_folder_with_its_notebooks_removes_them(config, vault):
    health = folder("Health")
    sub = folder("Labs")
    a, b = notebook("Blood pressure"), notebook("Cholesterol")
    client = TreeClient({"": [health], health["uuid"]: [a, sub], sub["uuid"]: [b]})
    engine_for(config, vault, client).sync_all()
    assert mirrored(vault, "Health", "Labs", "Cholesterol.md").is_file()

    client.tree = {"": [notebook("Other")]}
    engine_for(config, vault, client).sync_all()

    assert not mirrored(vault, "Health").exists()
    assert mirrored(vault, "Other.md").is_file()


def test_moving_a_notebook_moves_it_in_the_vault_even_if_unmodified(config, vault):
    health, work = folder("Health"), folder("Work")
    note = notebook("Ideas")
    client = TreeClient({"": [health, work], health["uuid"]: [note], work["uuid"]: []})
    engine_for(config, vault, client).sync_all()
    assert mirrored(vault, "Health", "Ideas.md").is_file()

    client.tree[health["uuid"]] = []
    client.tree[work["uuid"]] = [note]  # same lastModifiedTime
    engine_for(config, vault, client).sync_all()

    assert mirrored(vault, "Work", "Ideas.md").is_file()
    assert not mirrored(vault, "Health", "Ideas.md").exists()
    assert not mirrored(vault, "Health").exists()
    text = mirrored(vault, "Work", "Ideas.md").read_text(encoding="utf-8")
    assert f'resource_id: "{note["uuid"]}"' in text


def test_renaming_a_notebook_renames_the_note_and_its_attachments(config, vault):
    note = notebook("Ideas")
    client = TreeClient({"": [note]})
    engine_for(config, vault, client).sync_all()
    old_attachments = attachments(vault, note["uuid"])
    assert old_attachments and all(n.startswith("Ideas_") for n in old_attachments)

    client.tree[""] = [dict(note, name="Plans")]  # same lastModifiedTime
    engine_for(config, vault, client).sync_all()

    assert mirrored(vault, "Plans.md").is_file()
    assert not mirrored(vault, "Ideas.md").exists()
    new_attachments = attachments(vault, note["uuid"])
    assert new_attachments and all(n.startswith("Plans_") for n in new_attachments)


def test_a_page_removed_from_a_notebook_drops_its_image(config, vault):
    note = notebook("Ideas")
    client = TreeClient({"": [note]}, pages=2)
    engine_for(config, vault, client).sync_all()
    assert len(attachments(vault, note["uuid"])) == 2

    client.pages = 1
    client.tree[""] = [dict(note, lastModifiedTime=2000)]
    engine_for(config, vault, client).sync_all()

    assert len(attachments(vault, note["uuid"])) == 1


def test_two_notebooks_sharing_a_title_survive_the_other_being_deleted(config, vault):
    a, b = notebook("Ideas"), notebook("Ideas")
    client = TreeClient({"": [a, b]})
    engine_for(config, vault, client).sync_all()
    assert len(list(mirrored(vault).glob("*.md"))) == 2

    # The twin's file is still there while the survivor is processed, so the
    # survivor keeps its suffixed name for this sync...
    client.tree[""] = [b]
    engine_for(config, vault, client).sync_all()
    [survivor] = list(mirrored(vault).glob("*.md"))
    assert f'resource_id: "{b["uuid"]}"' in survivor.read_text(encoding="utf-8")

    # ...and takes the plain name on the next one.
    engine_for(config, vault, client).sync_all()
    assert [p.name for p in mirrored(vault).glob("*.md")] == ["Ideas.md"]
    assert len(attachments(vault, b["uuid"])) == 1


def test_deleting_a_dated_notebook_removes_its_journal_block(config, vault):
    config.create_missing_daily_notes = True
    config.obsidian_daily_notes = False
    note = notebook("2026-09-02")
    client = TreeClient({"": [note]})
    engine_for(config, vault, client).sync_all()
    daily = vault.daily_dir / "September" / "2026-09-02.md"
    assert "some text" in daily.read_text(encoding="utf-8")

    client.tree[""] = []
    client.count = 0
    engine_for(config, vault, client).sync_all()

    assert "some text" not in daily.read_text(encoding="utf-8")
    assert not mirrored(vault, "2026-09-02.md").exists()


def test_a_notebook_the_api_marks_deleted_is_treated_as_gone(config, vault):
    note = notebook("Ideas")
    client = TreeClient({"": [note]})
    engine_for(config, vault, client).sync_all()

    client.tree[""] = [dict(note, isDelete=1)]
    engine_for(config, vault, client).sync_all()

    assert not mirrored(vault, "Ideas.md").exists()
    assert note["uuid"] not in load_sync_state()["notes"]


def test_a_failed_listing_deletes_nothing(config, vault):
    note = notebook("Ideas")
    client = TreeClient({"": [note]})
    engine_for(config, vault, client).sync_all()

    client.fail = True
    with pytest.raises(RuntimeError):
        engine_for(config, vault, client).sync_all()

    assert mirrored(vault, "Ideas.md").is_file()
    assert note["uuid"] in load_sync_state()["notes"]


def test_an_empty_listing_for_a_non_empty_app_deletes_nothing(config, vault):
    note = notebook("Ideas")
    client = TreeClient({"": [note]})
    engine_for(config, vault, client).sync_all()

    client.tree = {"": []}
    client.count = 3  # the root says there is content, the listing says none
    engine_for(config, vault, client).sync_all()

    assert mirrored(vault, "Ideas.md").is_file()


def test_a_scoped_sync_only_prunes_its_own_apps(config, vault):
    note = notebook("Ideas")
    client = TreeClient({"": [note]}, app_type=1)
    engine_for(config, vault, client).sync_all()

    client.tree = {"": []}
    client.count = 0
    client.app_type = 2
    engine_for(config, vault, client).sync_all(app_types=[2])

    assert mirrored(vault, "Ideas.md").is_file()


def test_dry_run_reports_deletions_and_moves_without_touching_the_vault(config, vault):
    health, work = folder("Health"), folder("Work")
    moved, gone = notebook("Moves"), notebook("Goes")
    client = TreeClient({"": [health, work], health["uuid"]: [moved, gone], work["uuid"]: []})
    engine_for(config, vault, client).sync_all()

    client.tree[health["uuid"]] = []
    client.tree[work["uuid"]] = [moved]
    result = engine_for(config, vault, client).sync_all(dry_run=True)

    reasons = {p["name"]: p["reason"] for p in result["planned"]}
    assert reasons == {"Moves": "moved", "Goes": "deleted"}
    assert mirrored(vault, "Health", "Goes.md").is_file()
    assert mirrored(vault, "Health", "Moves.md").is_file()
    assert gone["uuid"] in load_sync_state()["notes"]


def test_deletion_can_be_switched_off(config, vault):
    config.remove_deleted_notebooks = False
    note = notebook("Ideas")
    client = TreeClient({"": [note]})
    engine_for(config, vault, client).sync_all()

    client.tree[""] = []
    client.count = 0
    result = engine_for(config, vault, client).sync_all()

    assert mirrored(vault, "Ideas.md").is_file()
    assert result["removed"] == []


def test_the_daily_app_state_is_never_pruned(config, vault):
    client = TreeClient({"": []}, count=0)
    engine = engine_for(config, vault, client)
    engine.state["notes"]["daily:2026-09-02"] = {"name": "Daily app: 2026-09-02", "app_type": "daily"}
    engine._dirty_notes.add("daily:2026-09-02")
    engine._save_state()

    engine_for(config, vault, client).sync_all()

    assert "daily:2026-09-02" in load_sync_state()["notes"]


def test_leftover_copies_from_an_earlier_move_are_cleaned_up_without_a_resync(config, vault):
    health = folder("Health")
    note = notebook("Ideas")
    client = TreeClient({"": [health], health["uuid"]: [note]})
    engine_for(config, vault, client).sync_all()
    # What older versions left behind after the notebook moved folders.
    stale = mirrored(vault, "Ideas.md")
    stale.write_text(mirrored(vault, "Health", "Ideas.md").read_text(encoding="utf-8"), encoding="utf-8")

    result = engine_for(config, vault, client).sync_all()  # nothing modified

    assert not stale.exists()
    assert mirrored(vault, "Health", "Ideas.md").is_file()
    assert result["total_synced"] == 0


def test_dry_run_reports_leftover_copies_without_removing_them(config, vault):
    health = folder("Health")
    note = notebook("Ideas")
    client = TreeClient({"": [health], health["uuid"]: [note]})
    engine_for(config, vault, client).sync_all()
    stale = mirrored(vault, "Ideas.md")
    stale.write_text(mirrored(vault, "Health", "Ideas.md").read_text(encoding="utf-8"), encoding="utf-8")

    result = engine_for(config, vault, client).sync_all(dry_run=True)

    assert [(p["name"], p["reason"]) for p in result["planned"]] == [("Ideas", "stale copy")]
    assert stale.exists()


# --- Deletion safety limit ---------------------------------------------------

def library(n):
    return [notebook(f"Note {i}") for i in range(n)]


def synced_library(config, vault, notes):
    client = TreeClient({"": list(notes)})
    engine_for(config, vault, client).sync_all()
    return client


def present(vault, notes):
    return [n["name"] for n in notes if mirrored(vault, f"{n['name']}.md").exists()]


def test_deleting_more_than_the_limit_is_held_back_until_confirmed(config, vault):
    config.max_deletions_per_sync = 2
    notes = library(12)
    client = synced_library(config, vault, notes)

    client.tree[""] = notes[3:]  # three notebooks gone
    result = engine_for(config, vault, client).sync_all()

    assert result["removed"] == []
    assert [h["name"] for h in result["held_back"]] == ["Note 0", "Note 1", "Note 2"]
    assert len(present(vault, notes)) == 12
    assert len(load_sync_state()["notes"]) == 12

    # Still held back on the next sync, and applied once the user confirms.
    assert engine_for(config, vault, client).sync_all()["removed"] == []
    result = engine_for(config, vault, client).sync_all(confirm_deletions=True)
    assert len(result["removed"]) == 3
    assert result["held_back"] == []
    assert present(vault, notes) == [n["name"] for n in notes[3:]]


def test_deleting_up_to_the_limit_goes_through(config, vault):
    config.max_deletions_per_sync = 2
    notes = library(12)
    client = synced_library(config, vault, notes)

    client.tree[""] = notes[2:]
    result = engine_for(config, vault, client).sync_all()

    assert len(result["removed"]) == 2
    assert result["held_back"] == []


def test_deleting_most_of_a_small_library_is_held_back_even_under_the_limit(config, vault):
    notes = library(4)
    client = synced_library(config, vault, notes)

    client.tree[""] = notes[3:]  # 3 of 4 gone, far below the default limit of 10
    client.count = 1
    result = engine_for(config, vault, client).sync_all()

    assert result["removed"] == []
    assert len(result["held_back"]) == 3
    assert len(present(vault, notes)) == 4


def test_deleting_half_a_small_library_is_not_held_back(config, vault):
    notes = library(4)
    client = synced_library(config, vault, notes)

    client.tree[""] = notes[2:]
    result = engine_for(config, vault, client).sync_all()

    assert len(result["removed"]) == 2


def test_a_limit_of_zero_switches_the_check_off(config, vault):
    config.max_deletions_per_sync = 0
    notes = library(12)
    client = synced_library(config, vault, notes)

    client.tree[""] = []
    client.count = 0
    result = engine_for(config, vault, client).sync_all()

    assert len(result["removed"]) == 12
    assert present(vault, notes) == []


def test_dry_run_says_which_deletions_would_be_held_back(config, vault):
    config.max_deletions_per_sync = 2
    notes = library(12)
    client = synced_library(config, vault, notes)

    client.tree[""] = notes[3:]
    result = engine_for(config, vault, client).sync_all(dry_run=True)

    assert sorted(p["reason"] for p in result["planned"]) == ["held back"] * 3
    assert len(result["held_back"]) == 3
    assert len(present(vault, notes)) == 12
