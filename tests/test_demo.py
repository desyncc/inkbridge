"""
contrib/demo: a fake Viwoods Cloud, so the dashboard can be run (and
photographed for the README) without anyone's real notebooks. The real
client and sync engine run against it, so it breaks here when the API they
expect changes.
"""

import importlib.util
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from viwoods.client import ViwoodsClient
from viwoods.sync import SyncEngine
from viwoods.vault import ObsidianVault

DEMO = Path(__file__).resolve().parent.parent / "contrib" / "demo" / "fake_viwoods.py"


@pytest.fixture(scope="module")
def fake():
    spec = importlib.util.spec_from_file_location("fake_viwoods", DEMO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(fake, config, tmp_path, monkeypatch):
    import viwoods.sync as sync_module

    monkeypatch.setattr(sync_module, "STATE_FILE", tmp_path / "sync_state.json")
    monkeypatch.setattr(sync_module, "LOCAL_CACHE_DIR", tmp_path / "cache")
    config.api_base_url = "http://fake.test"
    config.create_missing_daily_notes = True
    config.obsidian_daily_notes = False
    client = ViwoodsClient(config)
    client.http = TestClient(fake.build_app("http://fake.test"), base_url="http://fake.test")
    return SyncEngine(config, client=client, vault=ObsidianVault(config))


def test_a_full_sync_against_the_fake_cloud_fills_the_vault(engine):
    result = engine.sync_all()

    mirror = engine.vault.mirror_dir
    assert result["total_synced"] >= 6
    assert (mirror / "Paper" / "Projects" / "Garden plan.md").is_file()
    assert (mirror / "Paper" / "Journals" / "2026-09-02.md").is_file()
    assert (mirror / "Meeting").is_dir()
    assert result["incomplete"] == []


def test_transcripts_come_from_the_fake_cloud_not_an_ocr_engine(engine):
    engine.sync_all()

    note = (engine.vault.mirror_dir / "Paper" / "Projects" / "Garden plan.md").read_text(encoding="utf-8")
    assert "tomatoes" in note
    assert "![[" in note   # the page image is embedded


def test_a_dated_journal_lands_in_its_daily_note(engine):
    engine.sync_all()

    daily = engine.vault.daily_dir / "September" / "2026-09-02.md"
    assert daily.is_file()
    assert "morning walk" in daily.read_text(encoding="utf-8").lower()


def test_page_images_are_real_pngs(fake):
    client = TestClient(fake.build_app("http://fake.test"), base_url="http://fake.test")
    folder = client.post("/api/v1/resourceSync/getPage", json={"appType": 1, "resourceId": ""}).json()
    note = next(i for i in folder["data"]["list"] if i["resourceType"] != 0)

    detail = fake.page_urls(note["uuid"], "http://fake.test")
    image = client.get(detail[0].replace("http://fake.test", ""))

    assert image.status_code == 200
    assert Image.open(io.BytesIO(image.content)).format == "PNG"


def test_the_demo_has_no_real_names_in_it(fake):
    text = " ".join(
        page for notebook in fake.NOTEBOOKS for page in notebook["pages"]
    ).lower()
    for real in ("@", "http", "password"):
        assert real not in text


# --- the demo vault -------------------------------------------------------------

@pytest.fixture
def lived_in(fake, tmp_path, monkeypatch):
    """The seeded demo vault with a real sync into it, the way run_demo.py builds it."""
    import viwoods.sync as sync_module
    from viwoods.config import Config

    spec = importlib.util.spec_from_file_location("demo_vault", DEMO.parent / "demo_vault.py")
    demo_vault = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo_vault)

    monkeypatch.setattr(sync_module, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(sync_module, "LOCAL_CACHE_DIR", tmp_path / "cache")
    vault_dir = tmp_path / "vault"
    demo_vault.seed_vault(vault_dir)
    cfg = Config(token="demo", api_base_url="http://fake.test", vault_path=str(vault_dir),
                 obsidian_daily_notes=True)
    client = ViwoodsClient(cfg)
    client.http = TestClient(fake.build_app("http://fake.test"), base_url="http://fake.test")
    SyncEngine(cfg, client=client, vault=ObsidianVault(cfg)).sync_all()
    return vault_dir


def test_the_demo_daily_note_keeps_the_users_own_writing_around_the_block(lived_in):
    note = (lived_in / "10 - Journals" / "11 - Daily" / "2026-09-02.md").read_text(encoding="utf-8")

    assert "Coffee on the balcony" in note           # the user's text, untouched
    assert "## 🌅 Landing" in note and "## ✅ Check-ins" in note
    assert "%% viwoods:start %%" in note and "morning walk" in note.lower()
    assert note.index("Landing") < note.index("viwoods:start") < note.index("Check-ins")


def test_every_link_in_the_demo_notes_points_at_a_real_file(lived_in):
    import re

    def resolves(target: str) -> bool:
        # Obsidian accepts a path from the vault root or just a note's name.
        if (lived_in / target).is_file():   # an embedded image or other file
            return True
        return (lived_in / f"{target}.md").is_file() or any(lived_in.rglob(f"{Path(target).name}.md"))

    links, broken = 0, []
    for note in lived_in.rglob("*.md"):
        if ".obsidian" in note.parts:
            continue
        for target in re.findall(r"\[\[([^\]|#]+)", note.read_text(encoding="utf-8")):
            links += 1
            if not resolves(target):
                broken.append(f"{note.name} -> {target}")

    assert links >= 5, "the demo notes should link to the synced notebooks"
    assert not broken, broken


def test_the_vault_declares_its_daily_notes_the_way_obsidian_does(lived_in):
    import json

    settings = json.loads((lived_in / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8"))
    assert settings["folder"] == "10 - Journals/11 - Daily"
    assert (lived_in / "90 - Templates" / "Daily note.md").is_file()
