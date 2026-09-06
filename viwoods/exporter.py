import base64
import html
import io
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image as PILImage
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, PageBreak, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from .config import Config, load_config
from .sync import LOCAL_CACHE_DIR
from .client import ViwoodsClient
from .vault import ObsidianVault

DEFAULT_EXPORT_DIR = Path(__file__).resolve().parent.parent / "exports"


def _inline_markdown(text: str) -> str:
    """Escapes a line, then applies inline code / bold / italic."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", out)
    return out


def markdown_to_html(md_text: str) -> str:
    """
    Converts a transcript to block-level HTML, line by line.

    Markdown is line-oriented, so every heading/bullet decision has to be made
    before newlines are turned into <br/> — otherwise only the first line of
    the transcript can ever match `^# `.
    """
    lines = (md_text or "").replace("\r\n", "\n").split("\n")
    blocks: List[str] = []
    paragraph: List[str] = []
    list_items: List[str] = []
    list_tag = ""

    def flush_paragraph():
        if paragraph:
            blocks.append("<p>" + "<br/>".join(paragraph) + "</p>")
            paragraph.clear()

    def flush_list():
        nonlocal list_tag
        if list_items:
            body = "".join(f"<li>{item}</li>" for item in list_items)
            blocks.append(f"<{list_tag}>{body}</{list_tag}>")
            list_items.clear()
        list_tag = ""

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            flush_paragraph()
            flush_list()
            continue

        if line in ("---", "***", "___"):
            flush_paragraph()
            flush_list()
            blocks.append("<hr/>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^([-*+]|\d+\.)\s+(.*)$", line)
        if bullet:
            flush_paragraph()
            tag = "ol" if bullet.group(1).endswith(".") else "ul"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag

            item = bullet.group(2)
            checkbox = re.match(r"^\[([ xX])\]\s*(.*)$", item)
            if checkbox:
                checked = " checked" if checkbox.group(1).lower() == "x" else ""
                list_items.append(
                    f'<input type="checkbox" disabled{checked}/> {_inline_markdown(checkbox.group(2))}'
                )
            else:
                list_items.append(_inline_markdown(item))
            continue

        flush_list()
        paragraph.append(_inline_markdown(line))

    flush_paragraph()
    flush_list()
    return "\n".join(blocks)


def parse_markdown_to_flowables(md_text: str, styles) -> List[Any]:
    """Converts a Markdown string into ReportLab flowables."""
    flowables = []
    lines = md_text.strip().split("\n")
    in_code = False
    code_lines = []

    body_style = ParagraphStyle(
        "ExportBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=4
    )
    h1_style = ParagraphStyle(
        "ExportH1",
        parent=styles["Heading1"],
        fontSize=15,
        leading=19,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor("#0f172a")
    )
    h2_style = ParagraphStyle(
        "ExportH2",
        parent=styles["Heading2"],
        fontSize=12,
        leading=16,
        spaceBefore=8,
        spaceAfter=4,
        textColor=colors.HexColor("#1e293b")
    )
    h3_style = ParagraphStyle(
        "ExportH3",
        parent=styles["Heading3"],
        fontSize=10.5,
        leading=14,
        spaceBefore=6,
        spaceAfter=3,
        textColor=colors.HexColor("#334155")
    )
    bullet_style = ParagraphStyle(
        "ExportBullet",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13.5,
        leftIndent=14,
        spaceAfter=3,
        textColor=colors.HexColor("#334155")
    )
    code_style = ParagraphStyle(
        "ExportCode",
        parent=styles["Code"],
        fontSize=8.5,
        leading=11.5,
        fontName="Courier",
        backColor=colors.HexColor("#f1f5f9"),
        borderColor=colors.HexColor("#e2e8f0"),
        borderWidth=0.5,
        borderPadding=6,
        spaceAfter=6
    )

    for line in lines:
        s = line.strip()

        # Code block handling
        if s.startswith("```"):
            if in_code:
                in_code = False
                escaped_code = "<br/>".join(html.escape(c) for c in code_lines)
                flowables.append(Paragraph(escaped_code, code_style))
                flowables.append(Spacer(1, 4))
                code_lines = []
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not s:
            flowables.append(Spacer(1, 4))
            continue

        # Horizontal rule
        if s in ("---", "***", "___"):
            flowables.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cbd5e1"), spaceBefore=6, spaceAfter=6))
            continue

        # Headings
        if s.startswith("# "):
            flowables.append(Paragraph(html.escape(s[2:]), h1_style))
        elif s.startswith("## "):
            flowables.append(Paragraph(html.escape(s[3:]), h2_style))
        elif s.startswith("### "):
            flowables.append(Paragraph(html.escape(s[4:]), h3_style))
        elif s.startswith(("- [ ] ", "* [ ] ")):
            item = html.escape(s[6:])
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"\*(.+?)\*", r"<i>\1</i>", item)
            flowables.append(Paragraph(f"&#9633; {item}", bullet_style))
        elif s.startswith(("- [x] ", "* [x] ", "- [X] ", "* [X] ")):
            item = html.escape(s[6:])
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"\*(.+?)\*", r"<i>\1</i>", item)
            flowables.append(Paragraph(f"&#9632; {item}", bullet_style))
        elif s.startswith(("- ", "* ")):
            item = html.escape(s[2:])
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"\*(.+?)\*", r"<i>\1</i>", item)
            flowables.append(Paragraph(f"&bull; {item}", bullet_style))
        else:
            item = html.escape(s)
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"\*(.+?)\*", r"<i>\1</i>", item)
            flowables.append(Paragraph(item, body_style))

    return flowables


class NoteExporter:
    def __init__(
        self,
        config: Optional[Config] = None,
        vault: Optional[ObsidianVault] = None,
        client: Optional[ViwoodsClient] = None
    ):
        self.config = config or load_config()
        self.vault = vault or ObsidianVault(self.config)
        self.client = client or ViwoodsClient(self.config)
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    def list_notes(self) -> List[Dict[str, Any]]:
        """Returns all notes found in the Obsidian vault and local state."""
        notes = []
        mirror_root = self.vault.mirror_dir
        if mirror_root.exists():
            for root, dirs, files in os.walk(mirror_root):
                for f in files:
                    if f.endswith(".md"):
                        p = Path(root) / f
                        rel = p.relative_to(mirror_root).as_posix()
                        title = p.stem
                        uuid = ""
                        try:
                            head = p.read_text(encoding="utf-8", errors="ignore")[:600]
                            m = re.search(r'resource_id:\s*["\']?([^"\n\r]+)', head)
                            if m:
                                uuid = m.group(1).strip()
                        except Exception:
                            pass
                        notes.append({
                            "title": title,
                            "path": str(p),
                            "rel_path": rel,
                            "uuid": uuid,
                            "category": rel.split("/")[0] if "/" in rel else "Root"
                        })
        return sorted(notes, key=lambda x: x["title"].lower())

    def _stored_app_type(self, uuid: str) -> Optional[int]:
        """Returns the appType recorded for this note by the last sync, if any."""
        from .sync import load_sync_state
        note = load_sync_state().get("notes", {}).get(uuid)
        if isinstance(note, dict):
            app_type = note.get("app_type")
            if isinstance(app_type, int):
                return app_type
        return None

    def find_note(self, query: str, app_type: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Locates note data by UUID or title substring.
        Returns parsed note dictionary.
        """
        query_clean = query.strip()
        all_notes = self.list_notes()

        matched_path = None
        # 1. Try exact UUID match
        for n in all_notes:
            if n["uuid"] and n["uuid"].lower() == query_clean.lower():
                matched_path = Path(n["path"])
                break

        # 2. Try exact title match
        if not matched_path:
            for n in all_notes:
                if n["title"].lower() == query_clean.lower():
                    matched_path = Path(n["path"])
                    break

        # 3. Try case-insensitive substring
        if not matched_path:
            for n in all_notes:
                if query_clean.lower() in n["title"].lower():
                    matched_path = Path(n["path"])
                    break

        if matched_path and matched_path.exists():
            return self._parse_vault_note(matched_path)

        # 4. Fallback: try querying Viwoods Cloud directly if query is a UUID.
        # The caller's app type, else the one recorded at sync time, is tried
        # first; the remaining apps are only a last resort.
        if len(query_clean) >= 32 and "-" in query_clean:
            preferred = app_type or self._stored_app_type(query_clean)
            candidates = [t for t in (1, 2, 3, 4, 6) if t != preferred]
            if preferred:
                candidates.insert(0, preferred)

            for app_t in candidates:
                try:
                    detail = self.client.get_paper_detail(query_clean, app_type=app_t)
                except Exception:
                    continue
                if detail.get("imagePages"):
                    return self._parse_cloud_note(detail)

        return None

    def _parse_vault_note(self, md_path: Path) -> Dict[str, Any]:
        """Parses an Obsidian markdown note into structured pages and images."""
        text = md_path.read_text(encoding="utf-8")
        fm = {}
        fm_match = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip(" \"'")

        title = fm.get("title") or md_path.stem
        uuid = fm.get("resource_id", "")
        created = fm.get("created", datetime.now().strftime("%Y-%m-%d"))
        device = fm.get("device", "Viwoods AiPaper")

        pages = []
        page_chunks = re.split(r"\n## Page (\d+)\n", text)
        if len(page_chunks) > 1:
            for i in range(1, len(page_chunks), 2):
                pno = int(page_chunks[i])
                pbody = page_chunks[i + 1]

                img_match = re.search(r"!\[\[(.*?)\]\]", pbody)
                local_img_path = ""
                if img_match:
                    rel_img = img_match.group(1)
                    cand = (self.vault.vault_dir / rel_img).resolve()
                    if cand.exists():
                        local_img_path = str(cand)

                transcript = ""
                if "### 📝 Transcription" in pbody:
                    tpart = pbody.split("### 📝 Transcription", 1)[1]
                    tpart = re.split(r"\n---", tpart)[0]
                    transcript = tpart.strip()
                else:
                    tpart = re.sub(r"!\[\[.*?\]\]", "", pbody)
                    tpart = re.split(r"\n---", tpart)[0]
                    transcript = tpart.strip()

                pages.append({
                    "page_no": pno,
                    "image_path": local_img_path,
                    "transcript": transcript
                })
        else:
            # Note with no page headings (e.g. daily note or flat document)
            body = text
            if fm_match:
                body = text[fm_match.end():].strip()
            pages.append({
                "page_no": 1,
                "image_path": "",
                "transcript": body
            })

        return {
            "title": title,
            "uuid": uuid,
            "created": created,
            "device": device,
            "pages": pages,
            "source_path": str(md_path)
        }

    def _parse_cloud_note(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        """Parses a cloud-fetched note detail dictionary."""
        title = detail.get("name", "Untitled")
        uuid = detail.get("uuid", "")
        image_pages = detail.get("imagePages", [])
        cache_dir = LOCAL_CACHE_DIR

        pages = []
        for p in image_pages:
            pno = p.get("pageNo", 1)
            img_url = p.get("imageUrl", "")
            img_path = cache_dir / f"{uuid}_p{pno}.png"
            if img_url and not img_path.exists():
                try:
                    self.client.download_file(img_url, str(img_path))
                except Exception:
                    pass

            pages.append({
                "page_no": pno,
                "image_path": str(img_path) if img_path.exists() else "",
                "transcript": p.get("content", "")
            })

        return {
            "title": title,
            "uuid": uuid,
            "created": datetime.now().strftime("%Y-%m-%d"),
            "device": "Viwoods AiPaper",
            "pages": pages,
            "source_path": ""
        }

    def export_pdf(self, query: str, output_dir: Optional[str] = None, app_type: Optional[int] = None) -> Path:
        """
        Exports note to a high-quality, professional PDF.
        Features cover metadata banner, properly scaled handwriting scans,
        and cleanly typeset selectable Markdown transcripts.
        """
        note = self.find_note(query, app_type=app_type)
        if not note:
            raise ValueError(f"Note not found matching: '{query}'")

        out_dir = Path(output_dir or DEFAULT_EXPORT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.vault._sanitize_filename(note["title"])
        pdf_path = out_dir / f"{safe_name}.pdf"

        margin = 36  # 0.5 inch margins
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=letter,
            rightMargin=margin,
            leftMargin=margin,
            topMargin=margin,
            bottomMargin=margin
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "PdfTitle",
            parent=styles["Title"],
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#0f172a"),
            alignment=0,
            spaceAfter=4
        )
        meta_style = ParagraphStyle(
            "PdfMeta",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#64748b"),
            spaceAfter=12
        )
        page_head_style = ParagraphStyle(
            "PdfPageHead",
            parent=styles["Heading2"],
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#1e293b"),
            spaceBefore=10,
            spaceAfter=6
        )

        story = []

        # Title & Banner
        story.append(Paragraph(html.escape(note["title"]), title_style))
        meta_line = f"{note['device']} &bull; Created: {note['created']} &bull; {len(note['pages'])} Page(s)"
        story.append(Paragraph(meta_line, meta_style))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceBefore=2, spaceAfter=14))

        available_w = letter[0] - (2 * margin)  # 540 pt
        available_h = letter[1] - (2 * margin) - 60  # ~660 pt

        for idx, p in enumerate(note["pages"], start=1):
            if idx > 1:
                story.append(PageBreak())

            story.append(Paragraph(f"Page {p['page_no']}", page_head_style))

            img_p = p.get("image_path", "")
            transcript_text = p.get("transcript", "").strip()

            has_image = bool(img_p and os.path.exists(img_p))

            if has_image:
                try:
                    with PILImage.open(img_p) as pim:
                        iw, ih = pim.size

                    max_img_h = 360 if transcript_text else 600
                    scale = min(available_w / iw, max_img_h / ih)
                    draw_w = iw * scale
                    draw_h = ih * scale

                    story.append(RLImage(img_p, width=draw_w, height=draw_h))
                    story.append(Spacer(1, 10))
                except Exception as e:
                    print(f"Warning: Failed to render image {img_p} into PDF: {e}")

            if transcript_text:
                if has_image and len(transcript_text) > 400:
                    story.append(PageBreak())
                    story.append(Paragraph(f"Page {p['page_no']} &mdash; Transcription", page_head_style))

                flowables = parse_markdown_to_flowables(transcript_text, styles)
                story.extend(flowables)
                story.append(Spacer(1, 10))
            elif not has_image:
                story.append(Paragraph("<i>[Blank page]</i>", styles["Normal"]))

        doc.build(story)
        return pdf_path

    def export_html(self, query: str, output_dir: Optional[str] = None, app_type: Optional[int] = None) -> Path:
        """
        Exports note to a standalone, responsive HTML file with inlined base64 images.
        Opens in any browser, mobile or desktop, and can be printed cleanly via Ctrl+P.
        """
        note = self.find_note(query, app_type=app_type)
        if not note:
            raise ValueError(f"Note not found matching: '{query}'")

        out_dir = Path(output_dir or DEFAULT_EXPORT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.vault._sanitize_filename(note["title"])
        html_path = out_dir / f"{safe_name}.html"

        pages_html = []
        for p in note["pages"]:
            pno = p["page_no"]
            img_p = p.get("image_path", "")
            transcript = p.get("transcript", "")

            img_tag = ""
            if img_p and os.path.exists(img_p):
                try:
                    with open(img_p, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    img_tag = f'<div class="page-image-box"><img src="data:image/png;base64,{b64}" alt="Page {pno} handwriting"></div>'
                except Exception:
                    img_tag = ""

            rendered_transcript = markdown_to_html(transcript)

            transcript_box = f'<div class="transcript-box"><div class="transcript-label">📝 Transcription</div><div class="transcript-content">{rendered_transcript}</div></div>' if transcript else ''

            layout_class = "side-by-side" if (img_tag and transcript_box) else "single-col"

            page_block = f"""
            <section class="page-card">
              <div class="page-card-header">
                <span class="page-badge">Page {pno}</span>
              </div>
              <div class="page-card-body {layout_class}">
                {img_tag}
                {transcript_box}
              </div>
            </section>
            """
            pages_html.append(page_block)

        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(note['title'])} - Viwoods Note Export</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --primary: #2563eb;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 30px 20px 80px;
    }}
    .container {{
      max-width: 1050px;
      margin: 0 auto;
    }}
    .header-bar {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px 28px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .header-title h1 {{
      font-size: 24px;
      color: var(--text);
      margin-bottom: 6px;
    }}
    .header-meta {{
      font-size: 13px;
      color: var(--text-muted);
    }}
    .header-actions {{
      display: flex;
      gap: 10px;
    }}
    .btn {{
      background: var(--primary);
      color: #fff;
      border: none;
      padding: 9px 16px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      text-decoration: none;
    }}
    .btn:hover {{ opacity: 0.9; }}
    .btn-secondary {{
      background: #fff;
      color: var(--text);
      border: 1px solid var(--border);
    }}
    .btn-secondary:hover {{ background: #f1f5f9; }}
    .page-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      margin-bottom: 24px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .page-card-header {{
      background: #f8fafc;
      padding: 12px 20px;
      border-bottom: 1px solid var(--border);
    }}
    .page-badge {{
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
    }}
    .page-card-body {{
      padding: 20px;
    }}
    .side-by-side {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }}
    .page-image-box img {{
      max-width: 100%;
      height: auto;
      border-radius: 6px;
      border: 1px solid var(--border);
      display: block;
      background: #ffffff;
    }}
    .transcript-box {{
      background: #fcfcfd;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 18px 20px;
      font-size: 14px;
    }}
    .transcript-label {{
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 12px;
    }}
    .transcript-content {{
      font-family: inherit;
      word-break: break-word;
    }}
    .transcript-content > *:first-child {{ margin-top: 0; }}
    .transcript-content p {{ margin: 0 0 10px; }}
    .transcript-content h1 {{ font-size: 19px; margin: 16px 0 8px; }}
    .transcript-content h2 {{ font-size: 16px; margin: 14px 0 7px; }}
    .transcript-content h3 {{ font-size: 14.5px; margin: 12px 0 6px; }}
    .transcript-content h4,
    .transcript-content h5,
    .transcript-content h6 {{ font-size: 14px; margin: 12px 0 6px; }}
    .transcript-content ul,
    .transcript-content ol {{ margin: 0 0 10px; padding-left: 22px; }}
    .transcript-content li {{ margin-bottom: 3px; }}
    .transcript-content code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12.5px;
      background: #f1f5f9;
      padding: 1px 4px;
      border-radius: 4px;
    }}
    .transcript-content hr {{
      border: none;
      border-top: 1px solid var(--border);
      margin: 12px 0;
    }}
    @media (max-width: 768px) {{
      .side-by-side {{ grid-template-columns: 1fr; }}
      .header-bar {{ flex-direction: column; align-items: flex-start; gap: 14px; }}
    }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .header-actions {{ display: none !important; }}
      .page-card {{ border: none; box-shadow: none; page-break-after: always; }}
      .side-by-side {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="header-bar">
      <div class="header-title">
        <h1>{html.escape(note['title'])}</h1>
        <div class="header-meta">
          <span>{note['device']}</span> &bull;
          <span>{note['created']}</span> &bull;
          <span>{len(note['pages'])} Page(s)</span>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" onclick="window.print()">🖨️ Print / Save PDF</button>
      </div>
    </header>

    <main>
      {''.join(pages_html)}
    </main>
  </div>
</body>
</html>
"""
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(full_html)

        return html_path

    def export_zip(self, query: str, output_dir: Optional[str] = None, app_type: Optional[int] = None) -> Path:
        """
        Bundles note markdown and attachment images into a standard .zip file.
        """
        note = self.find_note(query, app_type=app_type)
        if not note:
            raise ValueError(f"Note not found matching: '{query}'")

        out_dir = Path(output_dir or DEFAULT_EXPORT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self.vault._sanitize_filename(note["title"])
        zip_path = out_dir / f"{safe_name}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            md_lines = [
                "---",
                f'title: "{safe_name}"',
                f'created: "{note["created"]}"',
                f'device: "{note["device"]}"',
                f'resource_id: "{note["uuid"]}"',
                "---",
                "",
                f"# {note['title']}",
                ""
            ]

            for p in note["pages"]:
                pno = p["page_no"]
                md_lines.append(f"## Page {pno}")
                img_p = p.get("image_path", "")
                if img_p and os.path.exists(img_p):
                    arc_img_name = f"attachments/{safe_name}_p{pno}.png"
                    zf.write(img_p, arc_img_name)
                    md_lines.append(f"![Page {pno}]({arc_img_name})")
                    md_lines.append("")

                tr = p.get("transcript", "")
                if tr:
                    md_lines.append(tr)
                    md_lines.append("")
                md_lines.append("---")
                md_lines.append("")

            zf.writestr(f"{safe_name}.md", "\n".join(md_lines))

        return zip_path
