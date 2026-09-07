"""Auto-tagging: sanitizing model output and writing it into notebook frontmatter."""

import pytest

from viwoods.ocr import OCREngine


@pytest.fixture
def ocr(config) -> OCREngine:
    return OCREngine(config)


def test_sanitize_tag_normalizes_to_kebab_case(ocr):
    assert ocr._sanitize_tag(" Project Phoenix ") == "project-phoenix"
    assert ocr._sanitize_tag("#already-tagged") == "already-tagged"
    assert ocr._sanitize_tag("Q3_Planning") == "q3-planning"


def test_sanitize_tag_strips_punctuation(ocr):
    assert ocr._sanitize_tag("finance/budget!!") == "finance/budget"
    assert ocr._sanitize_tag("...") == ""


def test_parse_tags_dedupes_and_caps(ocr):
    raw = "work, Work, project-x, meeting notes, personal"
    assert ocr._parse_tags(raw, max_tags=3) == ["work", "project-x", "meeting-notes"]


def test_parse_tags_drops_empty_candidates(ocr):
    assert ocr._parse_tags("work,, , travel", max_tags=5) == ["work", "travel"]


def test_parse_tags_strips_think_blocks(ocr):
    raw = "<think>reasoning about the note</think>work, travel"
    assert ocr._parse_tags(raw, max_tags=5) == ["work", "travel"]


def test_infer_tags_on_blank_text_short_circuits(ocr):
    assert ocr.infer_tags("   ") == []


def test_infer_tags_uses_cache_without_calling_the_engine(ocr, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not call the engine when cached")

    monkeypatch.setattr(ocr, "_chat_ollama", boom)
    key = ocr.tag_cache_key("some transcript text")
    ocr.tag_cache[key] = ["cached-tag"]

    assert ocr.infer_tags("some transcript text") == ["cached-tag"]


def test_infer_tags_calls_the_active_engine_and_caches_result(ocr, monkeypatch):
    monkeypatch.setattr(ocr, "_chat_ollama", lambda prompt: "budget, planning")

    tags = ocr.infer_tags("quarterly numbers and roadmap")

    assert tags == ["self/budget", "self/planning"]
    key = ocr.tag_cache_key("quarterly numbers and roadmap")
    assert ocr.tag_cache[key] == ["self/budget", "self/planning"]


def test_infer_tags_unknown_engine_returns_nothing(ocr, monkeypatch):
    ocr.config.ocr_engine = "windows"
    assert ocr.infer_tags("some text") == []


def test_mirror_notebook_appends_inferred_tags(vault):
    path = vault.mirror_notebook(
        rel_folder_path="Paper",
        notebook_name="Roadmap",
        uuid="uuid-1",
        pages_data=[],
        metadata={"inferred_tags": ["roadmap", "q3-planning"]},
    )

    content = path.read_text(encoding="utf-8")
    assert "  - viwoods\n  - notebook\n  - roadmap\n  - q3-planning\n" in content


def test_mirror_notebook_drops_inferred_tags_that_duplicate_base_tags(vault):
    path = vault.mirror_notebook(
        rel_folder_path="Paper",
        notebook_name="Duplicate",
        uuid="uuid-2",
        pages_data=[],
        metadata={"inferred_tags": ["notebook", "roadmap"]},
    )

    content = path.read_text(encoding="utf-8")
    assert "  - viwoods\n  - notebook\n  - roadmap\n" in content


def test_mirror_notebook_with_no_inferred_tags_is_unchanged(vault):
    path = vault.mirror_notebook(
        rel_folder_path="Paper",
        notebook_name="Plain",
        uuid="uuid-3",
        pages_data=[],
    )

    content = path.read_text(encoding="utf-8")
    assert "  - viwoods\n  - notebook\ndevice:" in content
