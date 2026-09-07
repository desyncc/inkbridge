"""Daily-journal injection: the markers must own their block and nothing else."""

import pytest

from viwoods.vault import VIWOODS_END, VIWOODS_START

from conftest import DAILY_HEADING as HEADING

LEGACY_NOTE = f"""---
date: 2026-09-02
---

## 🌅 Landing

Woke up early.

{HEADING}

previously injected transcript
#morningpages is a tag, not a heading

## ✅ Check-ins

- [ ] water the plants
"""


def note_path(vault, date_str="2026-09-02", month="September"):
    return vault.daily_dir / month / f"{date_str}.md"


def write_note(vault, content, date_str="2026-09-02", month="September", newline="\n"):
    path = note_path(vault, date_str, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.replace("\n", newline).encode("utf-8"))
    return path


def test_injects_between_markers(vault):
    out = vault.inject_journal_section("", HEADING, "uuid-a", "hello")

    assert VIWOODS_START in out and VIWOODS_END in out
    assert "%% viwoods:note uuid-a %%" in out
    assert out.index(VIWOODS_START) < out.index("hello") < out.index(VIWOODS_END)


def test_preserves_h2_section_after_the_heading(vault):
    out = vault.inject_journal_section(LEGACY_NOTE, HEADING, "uuid-a", "NEW")

    assert "## ✅ Check-ins" in out
    assert "- [ ] water the plants" in out
    assert "## 🌅 Landing" in out and "Woke up early." in out
    assert out.index(VIWOODS_END) < out.index("## ✅ Check-ins")


def test_legacy_content_is_replaced_and_tags_are_not_headings(vault):
    out = vault.inject_journal_section(LEGACY_NOTE, HEADING, "uuid-a", "NEW")

    # Both lines belonged to the old injected block, so both are replaced;
    # a `#tag` line must not be mistaken for the end of the section.
    assert "previously injected transcript" not in out
    assert "#morningpages" not in out
    assert "NEW" in out


def test_resync_replaces_only_its_own_block(vault):
    once = vault.inject_journal_section(LEGACY_NOTE, HEADING, "uuid-a", "FIRST")
    twice = vault.inject_journal_section(once, HEADING, "uuid-a", "SECOND")

    assert twice.count(VIWOODS_START) == 1
    assert "FIRST" not in twice and "SECOND" in twice
    assert "## ✅ Check-ins" in twice


def test_repeated_sync_is_idempotent(vault):
    once = vault.inject_journal_section(LEGACY_NOTE, HEADING, "uuid-a", "BODY")
    twice = vault.inject_journal_section(once, HEADING, "uuid-a", "BODY")

    assert twice == once


def test_two_notebooks_on_one_date_coexist(vault):
    out = vault.inject_journal_section(LEGACY_NOTE, HEADING, "uuid-a", "FROM A")
    out = vault.inject_journal_section(out, HEADING, "uuid-b", "FROM B")

    assert "FROM A" in out and "FROM B" in out

    out = vault.inject_journal_section(out, HEADING, "uuid-a", "A UPDATED")
    assert "A UPDATED" in out
    assert "FROM B" in out, "updating one notebook must not touch the other"
    assert "FROM A" not in out


def test_second_copy_of_the_heading_is_left_alone(vault):
    content = (
        f"{HEADING}\n\nfirst block\n\n"
        "## Something Else\n\n"
        f"{HEADING}\n\nquoted heading in user text\n"
    )

    out = vault.inject_journal_section(content, HEADING, "uuid-a", "BODY")

    assert "## Something Else" in out
    assert "quoted heading in user text" in out
    assert out.count(HEADING) == 2


def test_heading_matches_a_whole_line_only(vault):
    content = f"{HEADING} with a trailing phrase\n\nkeep me\n"

    out = vault.inject_journal_section(content, HEADING, "uuid-a", "BODY")

    assert "with a trailing phrase" in out
    assert "keep me" in out
    assert VIWOODS_START in out


def test_missing_heading_appends_without_disturbing_the_note(vault):
    content = "## Only Section\n\nuser text\n"

    out = vault.inject_journal_section(content, HEADING, "uuid-a", "BODY")

    assert out.startswith("## Only Section")
    assert "user text" in out
    assert HEADING in out and "BODY" in out


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_line_endings_are_preserved(vault, newline):
    path = write_note(vault, LEGACY_NOTE, newline=newline)

    vault.sync_daily_journal(
        "2026-09-02",
        [{"pageNo": 1, "transcript": "hand written"}],
        note_uuid="uuid-a",
    )

    raw = path.read_bytes().decode("utf-8")
    assert "hand written" in raw
    assert "## ✅ Check-ins" in raw
    if newline == "\r\n":
        assert raw.count("\n") == raw.count("\r\n"), "mixed line endings"
    else:
        assert "\r\n" not in raw


def test_sync_daily_journal_finds_the_note_outside_the_month_folder(vault):
    path = vault.daily_dir / "2026-09-02.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LEGACY_NOTE, encoding="utf-8")

    written = vault.sync_daily_journal(
        "2026-09-02", [{"pageNo": 1, "transcript": "flat layout"}], note_uuid="uuid-a"
    )

    assert written == path
    assert "flat layout" in path.read_text(encoding="utf-8")


def test_sync_daily_journal_creates_a_missing_note(vault):
    written = vault.sync_daily_journal(
        "2026-09-02", [{"pageNo": 1, "transcript": "brand new"}], note_uuid="uuid-a"
    )

    assert written == note_path(vault)
    text = written.read_text(encoding="utf-8")
    assert "brand new" in text and VIWOODS_START in text


def test_create_missing_can_be_disabled(vault):
    written = vault.sync_daily_journal(
        "2026-09-02",
        [{"pageNo": 1, "transcript": "x"}],
        note_uuid="uuid-a",
        create_missing=False,
    )

    assert written is None
    assert not note_path(vault).exists()
