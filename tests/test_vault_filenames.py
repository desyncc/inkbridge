"""Filename sanitizing and attachment naming."""

import pytest

LONG_TITLE = "Quarterly Planning And Retrospective Notes For The Whole Engineering Org"  # 71 chars
EIGHTY_CHARS = "A" * 80
UUID = "0d7e25d2-4f39-4122-affa-ea02704a5637"


def test_sanitize_replaces_illegal_characters(vault):
    assert vault._sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_collapses_newlines_and_trims(vault):
    assert vault._sanitize_filename("line\none\ttwo") == "line one two"
    # Leading/trailing dots and spaces go too, so no hidden or trailing-dot files.
    assert vault._sanitize_filename("  . surrounding dots .. ") == "surrounding dots"


def test_sanitize_falls_back_to_untitled(vault):
    assert vault._sanitize_filename("") == "Untitled"
    assert vault._sanitize_filename("...") == "Untitled"


def test_sanitize_truncates_the_stem_but_never_the_extension(vault):
    name = vault._sanitize_filename(EIGHTY_CHARS + ".png")

    assert name.endswith(".png")
    assert len(name) == 60


def test_sanitize_leaves_a_dot_that_is_not_an_extension(vault):
    assert vault._sanitize_filename("meeting v1.2 notes") == "meeting v1.2 notes"


@pytest.mark.parametrize("page_no", [1, 12, 100])
def test_attachment_name_keeps_uuid_page_and_extension(vault, page_no):
    name = vault.attachment_filename(EIGHTY_CHARS, UUID, page_no)

    assert name.endswith(f"_0d7e25d2_p{page_no}.png")
    # A final sanitize pass (as save_attachment does) must not change it.
    assert vault._sanitize_filename(name, max_length=100) == name


def test_attachment_pages_do_not_collide(vault):
    names = {vault.attachment_filename(LONG_TITLE, UUID, n) for n in range(1, 30)}

    assert len(names) == 29


def test_attachment_name_for_a_short_title_is_readable(vault):
    assert vault.attachment_filename("2026-09-02", UUID, 1) == "2026-09-02_0d7e25d2_p1.png"


def test_recording_attachment_naming(vault):
    name = vault.attachment_filename(LONG_TITLE, UUID, 2, ext=".m4a", kind="rec")

    assert name.endswith("_0d7e25d2_rec2.m4a")


def test_save_attachment_writes_the_full_name(vault, tmp_path):
    source = tmp_path / "page.png"
    source.write_bytes(b"png-bytes")

    saved = vault.save_attachment(
        str(source), vault.attachment_filename(EIGHTY_CHARS, UUID, 7)
    )

    assert saved.endswith("_0d7e25d2_p7.png")
    assert vault.attachments_dir.joinpath(saved.rsplit("/", 1)[-1]).read_bytes() == b"png-bytes"
