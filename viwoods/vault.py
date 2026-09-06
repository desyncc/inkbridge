import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config


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

    def _sanitize_filename(self, name: str) -> str:
        """Sanitizes filename for Windows filesystem and Obsidian link compatibility."""
        clean = re.sub(r'[\r\n\t]+', ' ', name)
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', clean).strip(". ")
        if len(sanitized) > 60:
            sanitized = sanitized[:60].rstrip()
        return sanitized or "Untitled"

    def get_attachment_obsidian_path(self, local_abs_path: str) -> str:
        """Converts an absolute attachment path to Obsidian [[wiki-link]] relative format."""
        rel = os.path.relpath(local_abs_path, self.vault_dir).replace("\\", "/")
        return rel

    def save_attachment(self, src_file: str, dest_filename: str) -> str:
        """Copies an image/audio to the vault attachments directory and returns its absolute path."""
        safe_name = self._sanitize_filename(dest_filename)
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
        created_time = metadata.get("createdAt") or metadata.get("creationTime")
        if isinstance(created_time, (int, float)):
            created_str = datetime.fromtimestamp(created_time / 1000).strftime("%Y-%m-%d")
        else:
            created_str = datetime.now().strftime("%Y-%m-%d")

        # Build Markdown content
        frontmatter = [
            "---",
            f"title: \"{safe_title}\"",
            f"created: \"{created_str}\"",
            f"updated: \"{updated_str}\"",
            "tags:",
            "  - viwoods",
            "  - notebook",
            f"device: \"{self.config.machine_model}\"",
            f"resource_id: \"{uuid}\"",
            f"total_pages: {len(pages_data)}",
            "transcribed: true",
            "---",
            ""
        ]

        body = [f"# {safe_title}", ""]

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

    def sync_daily_journal(
        self,
        date_str: str,
        pages_data: List[Dict[str, Any]],
        raw_meta: Optional[Dict[str, Any]] = None
    ) -> Optional[Path]:
        """
        Locates the user's daily journal note at:
        10 - Journals/<Month>/YYYY-MM-DD.md
        and non-destructively injects the transcribed text and page scans
        under the dedicated heading '# Transcribed text from AiPaper:'.
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

        # Format transcribed content to insert
        lines_to_inject = []
        for idx, p in enumerate(pages_data, start=1):
            page_no = p.get("pageNo", idx)
            img_path = p.get("local_image_path")
            if img_path and os.path.exists(img_path):
                wiki_link = self.get_attachment_obsidian_path(img_path)
                lines_to_inject.append(f"![[{wiki_link}]]\n")

            transcript = p.get("transcript", "").strip()
            if transcript:
                lines_to_inject.append(f"{transcript}\n")

        injected_block = "\n".join(lines_to_inject).strip()

        if target_note.exists():
            with open(target_note, "r", encoding="utf-8") as f:
                content = f.read()

            target_heading = self.config.daily_heading.strip()
            heading_pattern = rf"({re.escape(target_heading)})(.*)"

            if re.search(rf"^{re.escape(target_heading)}", content, re.MULTILINE):
                # Heading exists: replace content following heading
                # Split at heading
                parts = re.split(rf"(^{re.escape(target_heading)}\s*)", content, flags=re.MULTILINE)
                # parts[0]: before heading, parts[1]: heading, parts[2]: rest
                before = parts[0]
                heading = parts[1]
                after = parts[2] if len(parts) > 2 else ""

                # If there's a subsequent top-level heading (# Other), keep it
                after_match = re.search(r"\n(#[^#].*)", after)
                if after_match:
                    trailing = after[after_match.start():]
                else:
                    trailing = ""

                new_content = f"{before}{heading}\n\n{injected_block}\n{trailing}".rstrip() + "\n"
            else:
                # Heading doesn't exist yet: append cleanly to note
                new_content = content.rstrip() + f"\n\n---\n\n{target_heading}\n\n{injected_block}\n"

            with open(target_note, "w", encoding="utf-8") as f:
                f.write(new_content)

            return target_note
        else:
            # If the user hasn't created today's note yet, create it cleanly
            month_dir.mkdir(parents=True, exist_ok=True)
            template_content = (
                f"---\n"
                f"tags:\n"
                f"  - daily-journal\n"
                f"date: {date_str}\n"
                f"---\n\n"
                f"## 🗓️ Timeline\n\n"
                f"---\n\n"
                f"{self.config.daily_heading}\n\n"
                f"{injected_block}\n"
            )
            with open(target_note, "w", encoding="utf-8") as f:
                f.write(template_content)
            return target_note
