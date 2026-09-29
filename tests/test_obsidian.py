"""Obsidian's Daily notes settings: reading them, date formats, templates, and using them."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from viwoods.obsidian import (
    DailyNoteSettings, format_moment, read_daily_note_settings, render_template,
)
from viwoods.vault import ObsidianVault, VIWOODS_START

from conftest import DAILY_HEADING as HEADING

SEP_2 = datetime(2026, 9, 2)  # a Wednesday


# --- format_moment -----------------------------------------------------------

@pytest.mark.parametrize("fmt, expected", [
    ("YYYY-MM-DD", "2026-09-02"),
    ("YYYY/MM/YYYY-MM-DD", "2026/09/2026-09-02"),
    ("MMMM/YYYY-MM-DD", "September/2026-09-02"),
    ("dddd, MMMM Do YYYY", "Wednesday, September 2nd 2026"),
    ("ddd D MMM YY", "Wed 2 Sep 26"),
    ("YYYY-[W]WW-E", "2026-W36-3"),
    ("[Daily] YYYY.M.D", "Daily 2026.9.2"),
    ("DDDD Q", "245 3"),
])
def test_format_moment(fmt, expected):
    assert format_moment(SEP_2, fmt) == expected


def test_format_moment_iso_week_crosses_the_year():
    assert format_moment(datetime(2027, 1, 1), "GGGG-[W]WW") == "2026-W53"


def test_format_moment_locale_week_starts_on_sunday():
    # Moment's default locale: the week holding Jan 1 is week 1.
    assert format_moment(datetime(2026, 12, 27), "gggg-[w]ww") == "2027-w01"
    assert format_moment(datetime(2026, 12, 26), "gggg-[w]ww") == "2026-w52"


@pytest.mark.parametrize("day, expected", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
                                           (11, "11th"), (12, "12th"), (13, "13th"),
                                           (21, "21st"), (22, "22nd"), (23, "23rd")])
def test_format_moment_ordinals(day, expected):
    assert format_moment(datetime(2026, 9, day), "Do") == expected


# --- read_daily_note_settings -----------------------------------------------

def write_settings(vault_dir, settings):
    path = vault_dir / ".obsidian" / "daily-notes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings) if isinstance(settings, dict) else settings)


def test_no_settings_file(tmp_path):
    assert read_daily_note_settings(tmp_path) is None


def test_missing_keys_mean_obsidians_defaults(tmp_path):
    write_settings(tmp_path, {})
    assert read_daily_note_settings(tmp_path) == DailyNoteSettings("", "YYYY-MM-DD", "")


def test_settings_are_read_and_folder_slashes_trimmed(tmp_path):
    write_settings(tmp_path, {"folder": "Journal/", "format": "YYYY/MM/DD", "template": "T/Daily"})
    assert read_daily_note_settings(tmp_path) == DailyNoteSettings("Journal", "YYYY/MM/DD", "T/Daily")


def test_unreadable_settings_are_ignored(tmp_path):
    write_settings(tmp_path, "{not json")
    assert read_daily_note_settings(tmp_path) is None


# --- render_template ---------------------------------------------------------

def test_template_variables():
    now = datetime(2026, 9, 29, 14, 5)
    template = ("# {{title}}\ncreated {{date}} at {{time}}\n{{date:dddd}} / {{ time:HH.mm }}\n"
                "next: {{date+1d:YYYY-MM-DD}}\n[[{{yesterday}}]] [[{{tomorrow}}]]\n<% tp.date.now() %>")

    out = render_template(template, SEP_2, "YYYY-MM-DD", now=now)

    assert out == ("# 2026-09-02\ncreated 2026-09-02 at 14:05\nWednesday / 14.05\n"
                   "next: 2026-09-03\n[[2026-09-01]] [[2026-09-03]]\n<% tp.date.now() %>")


def test_template_title_is_the_file_name_not_the_folder_path():
    assert render_template("{{title}}", SEP_2, "YYYY/MM/YYYY-MM-DD") == "2026-09-02"


# --- The vault using them ----------------------------------------------------

@pytest.fixture
def obsidian_vault(config):
    config.create_missing_daily_notes = True
    write_settings(Path(config.vault_path), {
        "folder": "Journal", "format": "YYYY/MM/YYYY-MM-DD dddd", "template": "Templates/Daily",
    })
    return ObsidianVault(config)


def test_a_new_note_is_created_where_and_how_obsidian_would(obsidian_vault):
    template = obsidian_vault.vault_dir / "Templates" / "Daily.md"
    template.parent.mkdir(parents=True)
    template.write_text(f"# {{{{date:dddd, MMMM Do}}}}\n\n## Plans\n\n{HEADING}\n\n## Evening\n",
                        encoding="utf-8")

    written = obsidian_vault.sync_daily_journal(
        "2026-09-02", [{"pageNo": 1, "transcript": "hand written"}], note_uuid="uuid-a")

    assert written == obsidian_vault.vault_dir / "Journal/2026/09/2026-09-02 Wednesday.md"
    text = written.read_text(encoding="utf-8")
    assert text.startswith("# Wednesday, September 2nd\n\n## Plans\n")
    # Filled in under the template's own heading, not appended as a second one.
    assert text.count(HEADING) == 1
    assert text.index(HEADING) < text.index("hand written") < text.index("## Evening")


def test_an_existing_note_in_obsidians_format_is_found(obsidian_vault):
    note = obsidian_vault.vault_dir / "Journal/2026/09/2026-09-02 Wednesday.md"
    note.parent.mkdir(parents=True)
    note.write_text(f"my day\n\n{HEADING}\n", encoding="utf-8")

    written = obsidian_vault.sync_daily_journal(
        "2026-09-02", [{"pageNo": 1, "transcript": "found it"}], note_uuid="uuid-a")

    assert written == note
    assert "my day" in note.read_text(encoding="utf-8")
    assert "found it" in note.read_text(encoding="utf-8")


def test_a_missing_template_still_creates_the_note(obsidian_vault, capsys):
    written = obsidian_vault.sync_daily_journal(
        "2026-09-02", [{"pageNo": 1, "transcript": "no template"}], note_uuid="uuid-a")

    assert written.read_text(encoding="utf-8").startswith(f"{HEADING}\n{VIWOODS_START}")
    assert "could not be read" in capsys.readouterr().out


def test_stale_copies_are_found_in_custom_format_notes(obsidian_vault):
    obsidian_vault.sync_daily_journal(
        "2026-09-02", [{"pageNo": 1, "transcript": "old"}], note_uuid="uuid-a")
    keep = obsidian_vault.sync_daily_journal(
        "2026-09-03", [{"pageNo": 1, "transcript": "new"}], note_uuid="uuid-a")

    changed = obsidian_vault.remove_journal_block_elsewhere("uuid-a", keep=keep)

    assert [p.name for p in changed] == ["2026-09-02 Wednesday.md"]


def test_the_setting_can_be_turned_off(obsidian_vault, config):
    config.obsidian_daily_notes = False
    vault = ObsidianVault(config)

    assert vault.daily_settings is None
    assert vault.describe_daily_notes()["source"] == "config"
    assert vault.daily_note_path("2026-09-02") == vault.daily_dir / "September" / "2026-09-02.md"


def test_describe_daily_notes_reports_obsidians_settings(obsidian_vault):
    assert obsidian_vault.describe_daily_notes() == {
        "source": "obsidian", "folder": "Journal",
        "format": "YYYY/MM/YYYY-MM-DD dddd", "template": "Templates/Daily",
    }
