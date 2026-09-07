import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

# Delimiters for the block Viwoods owns inside a daily note. Everything
# outside START/END is the user's and is never touched.
VIWOODS_START = "<!-- viwoods:start -->"
VIWOODS_END = "<!-- viwoods:end -->"

# A daily note can be fed by more than one notebook, so each notebook gets
# its own sub-block keyed by uuid and is updated independently.
def _note_start_marker(uuid: str) -> str:
    return f"<!-- viwoods:note {uuid} -->"


def _note_end_marker(uuid: str) -> str:
    return f"<!-- viwoods:note-end {uuid} -->"


def _note_block(uuid: str, body: str) -> str:
    return f"{_note_start_marker(uuid)}\n{body.strip()}\n{_note_end_marker(uuid)}"


def _upsert_note_block(section: str, uuid: str, body: str) -> str:
    """Replaces this notebook's sub-block inside the section, or appends it."""
    start_marker = _note_start_marker(uuid)
    end_marker = _note_end_marker(uuid)
    block = _note_block(uuid, body)

    start_idx = section.find(start_marker)
    if start_idx != -1:
        end_idx = section.find(end_marker, start_idx)
        if end_idx != -1:
            tail = section[end_idx + len(end_marker):]
            return (section[:start_idx] + block + tail).strip("\n")
        # Dangling start marker (hand-edited): rewrite to the end of the section.
        return (section[:start_idx] + block).strip("\n")

    existing = section.strip("\n")
    if existing:
        return f"{existing}\n\n{block}"
    return block


def _to_date_str(value: Any) -> Optional[str]:
    """Best-effort YYYY-MM-DD from an epoch (s or ms) or a timestamp string."""
    if isinstance(value, bool) or value in (None, "", 0):
        return None

    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _to_date_str(int(text))
        for fmt, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
            try:
                return datetime.strptime(text[:width], fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _legacy_section_end(rest: str) -> int:
    """
    Offset in `rest` (the text following the target heading) where a legacy,
    marker-less injected section ends: the first heading line at any level or
    the first `---` rule. `#tag` lines are not headings.
    """
    offset = 0
    for line in rest.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if offset and (re.match(r"^#{1,6}[ \t]", stripped) or stripped.strip() == "---"):
            return offset
        offset += len(line)
    return len(rest)


class ObsidianVault:
    def __init__(self, config: Config):
        self.config = config
        self.vault_dir = Path(os.path.expanduser(config.vault_path)).resolve()
        self.mirror_dir = self.vault_dir / config.vault_mirror_folder
        self.attachments_dir = self.vault_dir / config.vault_attachments_folder
        self.daily_dir = self.vault_dir / config.daily_folder

        # Ensure directories exist
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, name: str, max_length: int = 60) -> str:
        """
        Sanitizes a filename for Windows filesystem and Obsidian link
        compatibility. Truncation applies to the stem only, so a file
        extension is never cut off.
        """
        clean = re.sub(r'[\r\n\t]+', ' ', name)
        stem, ext = os.path.splitext(clean)
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext):
            stem, ext = clean, ""

        stem = re.sub(r'[<>:"/\\|?*]', '_', stem).strip(". ")
        budget = max(1, max_length - len(ext))
        if len(stem) > budget:
            stem = stem[:budget].rstrip()

        return (stem or "Untitled") + ext

    def attachment_filename(
        self,
        note_name: str,
        uuid: str,
        page_no: Any,
        ext: str = ".png",
        kind: str = "p",
        title_limit: int = 40
    ) -> str:
        """
        Builds `<title>_<uuid8>_p<n>.png` (or `_rec<n>` for audio), truncating
        the *title* first so the uuid, index and extension always survive.
        """
        safe_title = self._sanitize_filename(note_name, max_length=title_limit)
        return f"{safe_title}_{(uuid or 'nouuid')[:8]}_{kind}{page_no}{ext}"

    def get_attachment_obsidian_path(self, local_abs_path: str) -> str:
        """Converts an absolute attachment path to Obsidian [[wiki-link]] relative format."""
        rel = os.path.relpath(local_abs_path, self.vault_dir).replace("\\", "/")
        return rel

    def save_attachment(self, src_file: str, dest_filename: str) -> str:
        """Copies an image/audio to the vault attachments directory and returns its absolute path."""
        # Generous limit: the caller has already budgeted the title, and the
        # uuid/page suffix must not be clipped.
        safe_name = self._sanitize_filename(dest_filename, max_length=100)
        dest_path = self.attachments_dir / safe_name
        shutil.copy2(src_file, dest_path)
        return str(dest_path)

    def mirror_notebook(
        self,
        rel_folder_path: str,
        notebook_name: str,
        uuid: str,
        pages_data: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        Creates or updates a mirrored notebook file in the vault:
        e.g., <vault>/Viwoods/Paper/Projects/MyNotes.md
        """
        if metadata is None:
            metadata = {}

        # Target directory under Viwoods/
        clean_rel = rel_folder_path.strip("/\\")
        target_dir = self.mirror_dir / clean_rel if clean_rel else self.mirror_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_title = self._sanitize_filename(notebook_name)
        target_file = target_dir / f"{safe_title}.md"

        # If a file with this title exists for a different note UUID, disambiguate with short uuid
        if target_file.exists():
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    content_head = f.read(500)
                if f'resource_id: "{uuid}"' not in content_head and f"resource_id: '{uuid}'" not in content_head:
                    target_file = target_dir / f"{safe_title} ({uuid[:6]}).md"
            except Exception:
                pass

        updated_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Creation time comes from the folder listing (the detail payload has
        # none); fall back to last-modified, then to today.
        created_str = None
        for key in ("created_time", "createTime", "createdAt", "create_time", "creationTime"):
            created_str = _to_date_str(metadata.get(key))
            if created_str:
                break
        if not created_str:
            created_str = _to_date_str(metadata.get("lastModifiedTime"))
        if not created_str:
            created_str = datetime.now().strftime("%Y-%m-%d")

        total_available = metadata.get("total_pages_available") or len(pages_data)
        page_cap = metadata.get("page_cap") or 0
        pages_dropped = max(0, int(total_available) - len(pages_data))

        base_tags = ["viwoods", "notebook"]
        inferred_tags = [t for t in (metadata.get("inferred_tags") or []) if t not in base_tags]

        # Build Markdown content
        frontmatter = [
            "---",
            f"title: \"{safe_title}\"",
            f"created: \"{created_str}\"",
            f"updated: \"{updated_str}\"",
            "tags:",
            *(f"  - {tag}" for tag in base_tags + inferred_tags),
            f"device: \"{self.config.machine_model}\"",
            f"resource_id: \"{uuid}\"",
            f"total_pages: {len(pages_data)}",
            f"total_pages_available: {total_available}",
            "transcribed: true",
            "---",
            ""
        ]

        body = [f"# {safe_title}", ""]

        if pages_dropped:
            body.append(
                f"> [!warning] {len(pages_data)} of {total_available} pages synced, "
                f"cap = max_pages_per_notebook ({page_cap}). Raise the cap to sync the rest."
            )
            body.append("")

        if not pages_data:
            body.append("*[Empty notebook or collection]*\n")

        for idx, p in enumerate(pages_data, start=1):
            page_no = p.get("pageNo", idx)
            body.append(f"## Page {page_no}")

            img_path = p.get("local_image_path")
            if img_path and os.path.exists(img_path):
                wiki_link = self.get_attachment_obsidian_path(img_path)
                body.append(f"![[{wiki_link}]]")
                body.append("")

            transcript = p.get("transcript", "").strip()
            if transcript:
                if img_path:
                    body.append("### 📝 Transcription")
                body.append(transcript)
                body.append("")
            elif not img_path:
                body.append("*[Blank page]*")
                body.append("")

            body.append("---")
            body.append("")

        # Append recordings if any
        recordings = metadata.get("recordings", [])
        if recordings:
            body.append("## 🎙️ Audio Recordings")
            for r in recordings:
                rec_name = r.get("recordingName", "Audio Clip")
                rec_path = r.get("local_audio_path")
                if rec_path:
                    rec_link = self.get_attachment_obsidian_path(rec_path)
                    body.append(f"- **{rec_name}**: ![[{rec_link}]]")
                transcript_text = r.get("transcriptText", "").strip()
                if transcript_text:
                    body.append(f"  > 🗣️ *Transcript*: {transcript_text}")
            body.append("")

        full_content = "\n".join(frontmatter + body)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(full_content)

        return target_file

    def render_journal_body(self, pages_data: List[Dict[str, Any]]) -> str:
        """Renders one notebook's pages into the markdown injected into a daily note."""
        lines_to_inject = []
        for idx, p in enumerate(pages_data, start=1):
            img_path = p.get("local_image_path")
            if img_path and os.path.exists(img_path):
                wiki_link = self.get_attachment_obsidian_path(img_path)
                lines_to_inject.append(f"![[{wiki_link}]]\n")

            transcript = (p.get("transcript") or "").strip()
            if transcript:
                lines_to_inject.append(f"{transcript}\n")

        return "\n".join(lines_to_inject).strip()

    def inject_journal_section(self, content: str, heading: str, uuid: str, body: str) -> str:
        """
        Injects `body` into `content` under `heading`, between explicit
        <!-- viwoods:start --> / <!-- viwoods:end --> markers, touching nothing
        outside them. Only the sub-block belonging to `uuid` is rewritten.
        """
        heading = heading.strip()
        match = re.search(rf"^{re.escape(heading)}[ \t]*$", content, re.MULTILINE)

        if not match:
            # Heading absent: append a fresh, fully marked section.
            section = _upsert_note_block("", uuid, body)
            prefix = content.rstrip("\n")
            separator = "\n\n---\n\n" if prefix else ""
            return f"{prefix}{separator}{heading}\n{VIWOODS_START}\n{section}\n{VIWOODS_END}\n"

        # Only the first occurrence of the heading is used; any later copy is
        # ordinary user content and is left alone.
        head_end = match.end()
        rest = content[head_end:]

        start_idx = rest.find(VIWOODS_START)
        end_idx = rest.find(VIWOODS_END, start_idx + len(VIWOODS_START)) if start_idx != -1 else -1

        if start_idx != -1 and end_idx != -1:
            gap = rest[:start_idx]
            section = rest[start_idx + len(VIWOODS_START):end_idx]
            tail = rest[end_idx + len(VIWOODS_END):]
            new_section = _upsert_note_block(section, uuid, body)
            return (
                content[:head_end] + gap + VIWOODS_START + "\n" +
                new_section + "\n" + VIWOODS_END + tail
            )

        # Legacy note: heading present but never marked. Wrap the region from
        # the heading up to the next heading / rule and take ownership of it.
        stop = _legacy_section_end(rest)
        tail = rest[stop:]
        new_section = _upsert_note_block("", uuid, body)
        rebuilt = (
            content[:head_end] + "\n" + VIWOODS_START + "\n" +
            new_section + "\n" + VIWOODS_END + "\n"
        )
        if tail.strip():
            rebuilt += "\n" + tail.lstrip("\n")
        return rebuilt

    def sync_daily_journal(
        self,
        date_str: str,
        pages_data: List[Dict[str, Any]],
        raw_meta: Optional[Dict[str, Any]] = None,
        note_uuid: Optional[str] = None,
        create_missing: Optional[bool] = None
    ) -> Optional[Path]:
        """
        Locates the user's daily journal note at:
        10 - Journals/<Month>/YYYY-MM-DD.md
        and injects the transcribed text and page scans between explicit
        markers under the heading '# Transcribed text from AiPaper:'.
        """
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            dt = datetime.now()

        month_name = dt.strftime("%B")  # e.g., "September", "July"
        month_dir = self.daily_dir / month_name
        target_note = month_dir / f"{date_str}.md"

        # If not in <Month>/ directory, check direct daily folder
        if not target_note.exists():
            fallback_note = self.daily_dir / f"{date_str}.md"
            if fallback_note.exists():
                target_note = fallback_note

        uuid = note_uuid or "default"
        body = self.render_journal_body(pages_data)
        heading = self.config.daily_heading.strip()

        if target_note.exists():
            raw = target_note.read_bytes()
            newline = "\r\n" if b"\r\n" in raw else "\n"
            content = raw.decode("utf-8").replace("\r\n", "\n")

            new_content = self.inject_journal_section(content, heading, uuid, body)

            with open(target_note, "w", encoding="utf-8", newline=newline) as f:
                f.write(new_content)

            return target_note

        if create_missing is None:
            create_missing = getattr(self.config, "create_missing_daily_notes", True)
        if not create_missing:
            return None

        # If the user hasn't created today's note yet, create it cleanly
        month_dir.mkdir(parents=True, exist_ok=True)
        section = _upsert_note_block("", uuid, body)
        template_content = (
            f"---\n"
            f"tags:\n"
            f"  - daily-journal\n"
            f"date: {date_str}\n"
            f"---\n\n"
            f"## 🗓️ Timeline\n\n"
            f"---\n\n"
            f"{heading}\n"
            f"{VIWOODS_START}\n"
            f"{section}\n"
            f"{VIWOODS_END}\n"
        )
        with open(target_note, "w", encoding="utf-8") as f:
            f.write(template_content)
        return target_note
