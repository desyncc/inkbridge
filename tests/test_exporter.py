"""Note lookup and the markdown -> HTML conversion used by the HTML export."""

import pytest

from viwoods.exporter import NoteExporter, markdown_to_html

UUID_A = "0d7e25d2-4f39-4122-affa-ea02704a5637"
UUID_B = "1a2b3c4d-5e6f-4788-9900-aabbccddeeff"


def write_note(vault, rel_path, title, uuid, body="## Page 1\n\ntext\n"):
    path = vault.mirror_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{title}"\nresource_id: "{uuid}"\ncreated: "2026-09-01"\n'
        f'device: "AiPaper"\n---\n\n# {title}\n\n{body}',
        encoding="utf-8",
    )
    return path


class RecordingClient:
    """Stands in for ViwoodsClient, remembering which app types were tried."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.tried = []

    def get_paper_detail(self, uuid, app_type=1):
        self.tried.append(app_type)
        if app_type in self.answers:
            return self.answers[app_type]
        raise RuntimeError("not found")


@pytest.fixture
def exporter(config, vault):
    write_note(vault, "Paper/Morning Pages.md", "Morning Pages", UUID_A)
    write_note(vault, "Meeting/Team Sync.md", "Team Sync", UUID_B)
    return NoteExporter(config=config, vault=vault, client=RecordingClient())


def test_list_notes_finds_every_mirrored_note(exporter):
    titles = [n["title"] for n in exporter.list_notes()]

    assert titles == ["Morning Pages", "Team Sync"]
    assert exporter.list_notes()[0]["uuid"] == UUID_A


def test_find_note_by_uuid(exporter):
    assert exporter.find_note(UUID_B)["title"] == "Team Sync"


def test_find_note_by_exact_title(exporter):
    assert exporter.find_note("Team Sync")["uuid"] == UUID_B


def test_find_note_by_exact_title_is_case_insensitive(exporter):
    assert exporter.find_note("team sync")["uuid"] == UUID_B


def test_find_note_by_substring(exporter):
    assert exporter.find_note("Morning")["uuid"] == UUID_A


def test_find_note_prefers_an_exact_title_over_a_substring(config, vault):
    write_note(vault, "Paper/Sync.md", "Sync", UUID_A)
    write_note(vault, "Paper/Team Sync Notes.md", "Team Sync Notes", UUID_B)
    exporter = NoteExporter(config=config, vault=vault, client=RecordingClient())

    assert exporter.find_note("Sync")["title"] == "Sync"


def test_find_note_returns_none_when_nothing_matches(exporter):
    assert exporter.find_note("nothing like this") is None


def test_find_note_uses_the_given_app_type_first(config, vault):
    detail = {"uuid": UUID_A, "name": "Cloud Note", "imagePages": [{"pageNo": 1, "content": "hi"}]}
    client = RecordingClient({4: detail})
    exporter = NoteExporter(config=config, vault=vault, client=client)

    note = exporter.find_note(UUID_A, app_type=4)

    assert note["title"] == "Cloud Note"
    assert client.tried == [4], "the caller's app type must be tried first"


def test_find_note_falls_back_to_the_app_type_recorded_at_sync_time(config, vault, monkeypatch):
    import viwoods.sync as sync_module

    monkeypatch.setattr(
        sync_module, "load_sync_state",
        lambda: {"notes": {UUID_A: {"name": "n", "app_type": 6}}, "last_full_sync": None},
    )
    detail = {"uuid": UUID_A, "name": "Memo", "imagePages": [{"pageNo": 1, "content": "hi"}]}
    client = RecordingClient({6: detail})
    exporter = NoteExporter(config=config, vault=vault, client=client)

    assert exporter.find_note(UUID_A)["title"] == "Memo"
    assert client.tried[0] == 6


# --- markdown_to_html -----------------------------------------------------

def test_every_heading_is_converted_not_just_the_first():
    out = markdown_to_html("# One\ntext\n## Two\n### Three")

    assert "<h1>One</h1>" in out
    assert "<h2>Two</h2>" in out
    assert "<h3>Three</h3>" in out


def test_paragraph_lines_are_joined_with_breaks():
    out = markdown_to_html("first line\nsecond line")

    assert out == "<p>first line<br/>second line</p>"


def test_blank_lines_separate_paragraphs():
    out = markdown_to_html("one\n\ntwo")

    assert out == "<p>one</p>\n<p>two</p>"


def test_bullets_and_checkboxes():
    out = markdown_to_html("- [x] done\n- [ ] todo\n- plain")

    assert out.startswith("<ul>") and out.endswith("</ul>")
    assert 'disabled checked/> done' in out
    assert 'disabled/> todo' in out


def test_numbered_lists_use_ol():
    assert markdown_to_html("1. one\n2. two").startswith("<ol>")


def test_rules_and_inline_formatting():
    out = markdown_to_html("---\n**bold** and *italic* and `code`")

    assert "<hr/>" in out
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<code>code</code>" in out


def test_html_in_a_transcript_is_escaped():
    out = markdown_to_html("# <script>alert(1)</script>")

    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_empty_transcript_produces_nothing():
    assert markdown_to_html("") == ""


def test_export_html_renders_every_heading(exporter, tmp_path):
    write_note(
        exporter.vault, "Paper/Morning Pages.md", "Morning Pages", UUID_A,
        body="## Page 1\n\n### 📝 Transcription\n# Big\nbody text\n## Sub\n- item\n",
    )

    out_file = exporter.export_html("Morning Pages", output_dir=str(tmp_path / "out"))
    html_text = out_file.read_text(encoding="utf-8")

    assert "<h1>Big</h1>" in html_text
    assert "<h2>Sub</h2>" in html_text
    assert "<li>item</li>" in html_text
