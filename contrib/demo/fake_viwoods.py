"""
A pretend Viwoods Cloud, for trying InkBridge without anyone's real notebooks.

It answers the handful of calls the client makes (devices, folders, a notebook's
pages, page images) with a small made-up library, so the real client, sync
engine and dashboard run against it unchanged. The page images are generated:
ruled paper with jittery "handwriting" that matches each page's text, which is
also sent as the page's transcript, so no OCR engine is needed.

Run it with contrib/demo/run_demo.py. Everything here is invented.
"""

import base64
import functools
import gzip
import io
import json
import random
import uuid
from datetime import datetime
from typing import Any, Dict, List

from fastapi import FastAPI, Response
from PIL import Image, ImageDraw, ImageFont

NAMESPACE = uuid.UUID("0d1c4d7e-5a3b-4f5e-9a0b-2f3c6c1d8e11")
PAGE_SIZE = (954, 1272)
PAPER = (251, 247, 238)
RULE = (214, 226, 238)
MARGIN = (232, 160, 160)
INK = (24, 52, 96)
LINE_PITCH = 58

# appType numbers are the ones the real API uses.
APPS = {1: "Paper", 2: "Meeting", 3: "Learning", 4: "Knowledge Base", 6: "Memo"}
SYNC_ENDPOINTS = {
    "paperSync": 1, "meetingSync": 2, "learningSync": 3, "knowledgeBaseSync": 4, "memoSync": 6,
}


def _id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def _ms(month: int, day: int, hour: int = 9) -> int:
    return int(datetime(2026, month, day, hour).timestamp() * 1000)


FOLDERS: List[Dict[str, Any]] = [
    {"name": "Journals", "app": 1, "parent": None},
    {"name": "Projects", "app": 1, "parent": None},
    {"name": "Reference", "app": 1, "parent": None},
]

NOTEBOOKS: List[Dict[str, Any]] = [
    {"name": "2026-09-02", "app": 1, "parent": "Journals", "modified": _ms(9, 2, 21), "pages": [
        "Tuesday 2 Sept\n\n- Morning walk by the river, 40 min\n- Call the dentist to move Thursday\n"
        "- Draft the quarterly summary\n\nEvening: finally fixed the bike chain.\n"
        "Tomorrow: groceries + library return",
    ]},
    {"name": "2026-09-03", "app": 1, "parent": "Journals", "modified": _ms(9, 3, 20), "pages": [
        "Wednesday 3 Sept\n\nSlept badly, but the walk helped.\nLibrary: return 2 books, renew 1\n"
        "Cooked the lentil stew again - needs more lemon.\nBook club on Friday",
    ]},
    {"name": "Garden plan", "app": 1, "parent": "Projects", "modified": _ms(9, 4, 10), "pages": [
        "Garden plan - spring\n\nBeds: tomatoes (south), beans (east),\nherbs near the door\n\n"
        "To buy: compost x4, cedar stakes,\nseed trays\n\nFrost risk until mid-April",
        "Watering schedule\n\nMon / Thu: tomatoes\nTue / Fri: beans\nDaily: seedlings (morning)\n\n"
        "Check the rain barrel on Sundays",
    ]},
    {"name": "Home network", "app": 1, "parent": "Projects", "modified": _ms(9, 1, 16), "pages": [
        "Home network\n\nrouter -> switch -> NAS\nstatic: .10 NAS   .20 printer\n\n"
        "TODO: change the router login\nTODO: label the cables",
    ]},
    {"name": "Sourdough notes", "app": 1, "parent": "Reference", "modified": _ms(8, 28, 8), "pages": [
        "Sourdough\n\n500 g flour, 350 g water,\n100 g starter, 10 g salt\n\n"
        "Bulk 5 h at 24 C, fold every 30 min\nBake 240 C covered 20 min,\nuncovered 25 min",
    ]},
    {"name": "Reading list", "app": 1, "parent": "Reference", "modified": _ms(8, 30, 19), "pages": [
        "Reading list\n\n1. A Pattern Language\n2. The Design of Everyday Things\n3. Godel, Escher, Bach (re-read)",
    ]},
    {"name": "Quick notes", "app": 1, "parent": None, "modified": _ms(9, 4, 7), "pages": [
        "Ideas\n\n- back up photos to the NAS\n- ask Sam about the book club\n- try the new pour-over ratio",
    ]},
    {"name": "Team sync 09-04", "app": 2, "parent": None, "modified": _ms(9, 4, 15), "pages": [
        "Team sync - 4 Sept\n\nAttendees: Ana, Ben, Chloe\n\nDecisions: ship v2 on the 15th\n"
        "Ben owns the migration\n\nActions: Chloe - update docs\nAna - book the review",
    ]},
]


def _item(entry: Dict[str, Any], is_folder: bool) -> Dict[str, Any]:
    return {
        "uuid": _id(("folder:" if is_folder else "note:") + entry["name"]),
        "name": entry["name"],
        "appType": entry["app"],
        "resourceType": 0 if is_folder else 1,
        "subTab": -1,
        "lastModifiedTime": entry.get("modified", _ms(9, 1)),
        "createTime": entry.get("modified", _ms(9, 1)) - 3_600_000,
    }


def _children(app: int, parent_id: str) -> List[Dict[str, Any]]:
    """The folders and notebooks directly inside `parent_id` ("" = the app's top level)."""
    items = []
    if not parent_id:
        items += [_item(f, True) for f in FOLDERS if f["app"] == app]
    for notebook in NOTEBOOKS:
        if notebook["app"] != app:
            continue
        parent = notebook["parent"]
        at_top = parent is None and not parent_id
        in_folder = parent is not None and _id("folder:" + parent) == parent_id
        if at_top or in_folder:
            items.append(_item(notebook, False))
    return items


def _find_note(note_uuid: str) -> Dict[str, Any]:
    return next(n for n in NOTEBOOKS if _id("note:" + n["name"]) == note_uuid)


def page_urls(note_uuid: str, base_url: str) -> List[str]:
    note = _find_note(note_uuid)
    return [f"{base_url}/img/{note_uuid}/{n}.png" for n in range(1, len(note["pages"]) + 1)]


def _encode(obj: Any) -> str:
    return base64.b64encode(gzip.compress(json.dumps(obj).encode("utf-8"))).decode("ascii")


# --- the page images ---------------------------------------------------------

_FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
    "C:/Windows/Fonts/ariali.ttf",
]


@functools.lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _write(img: Image.Image, rng: random.Random, text: str, x: int, y: int, size: int) -> None:
    """Draws `text` one letter at a time, each slightly off the line and tilted, like a pen."""
    font = _font(size)
    for ch in text:
        if ch != " ":
            glyph = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(glyph).text((size // 2, size // 4), ch, font=font, fill=INK + (255,))
            glyph = glyph.rotate(rng.uniform(-4, 4), resample=Image.BICUBIC)
            img.paste(glyph, (x - size // 2, y - size // 4 + rng.randint(-3, 3)), glyph)
        x += int(font.getlength(ch)) + rng.randint(-1, 2)


@functools.lru_cache(maxsize=None)
def render_page(text: str) -> bytes:
    """A page of ruled paper with `text` handwritten on it, as PNG bytes. Same text, same image."""
    rng = random.Random(text)
    img = Image.new("RGB", PAGE_SIZE, PAPER)
    draw = ImageDraw.Draw(img)
    for y in range(LINE_PITCH * 2, PAGE_SIZE[1], LINE_PITCH):
        draw.line([(0, y), (PAGE_SIZE[0], y)], fill=RULE, width=2)
    draw.line([(96, 0), (96, PAGE_SIZE[1])], fill=MARGIN, width=2)

    for row, line in enumerate(text.split("\n")):
        baseline = LINE_PITCH * 2 + row * LINE_PITCH
        title = row == 0
        _write(img, rng, line, 120, baseline - (34 if title else 26), 46 if title else 36)
        if title:   # a wobbly underline
            points = [(120 + i * 12, baseline + 4 + rng.randint(-2, 2)) for i in range(0, 38)]
            draw.line(points, fill=INK, width=3)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# --- the API -----------------------------------------------------------------

def _ok(data: Any) -> Dict[str, Any]:
    return {"code": 200, "msg": "ok", "data": data}


def build_app(base_url: str) -> FastAPI:
    """The fake cloud. `base_url` is where the page images will be fetched from."""
    app = FastAPI(title="Fake Viwoods Cloud (demo)")

    @app.post("/api/v1/userDevice")
    def devices():
        return _ok({"list": [{"machineNumber": "DEMO-0001", "modelName": "AiPaper"}]})

    @app.post("/api/v1/user/refreshLogin")
    def refresh():
        return _ok({"token": "demo-token"})

    @app.post("/api/v1/resourceSync/getRootFolder")
    def root_folders():
        return _ok([
            {"name": name, "appType": app_type, "count": len(_children(app_type, ""))}
            for app_type, name in APPS.items()
        ])

    @app.post("/api/v1/resourceSync/getPage")
    def folder_items(body: Dict[str, Any]):
        items = _children(int(body.get("appType", 1)), body.get("resourceId") or "")
        return _ok({"list": items, "total": len(items)})

    @app.post("/api/v1/{endpoint}/get")
    def notebook(endpoint: str, body: Dict[str, Any]):
        if endpoint not in SYNC_ENDPOINTS:
            return {"code": 404, "msg": "unknown endpoint"}
        note = _find_note(body["uuid"])
        pages = [
            {"pageNo": n, "imageUrl": url, "content": text}
            for n, (url, text) in enumerate(zip(page_urls(body["uuid"], base_url), note["pages"]), start=1)
        ]
        meta = {"paperMeeting": {"name": note["name"], "imagePages": pages, "recordings": []}}
        return _ok({"meta": _encode(meta), "data": _encode({}), "name": note["name"]})

    @app.post("/api/v1/resourceDailySync/getList")
    def daily_list():
        return _ok([])

    @app.post("/api/v1/resourceDailySync/getDateList")
    def daily_dates():
        return _ok([])

    @app.get("/img/{note_uuid}/{page}.png")
    def image(note_uuid: str, page: int):
        note = _find_note(note_uuid)
        return Response(content=render_page(note["pages"][page - 1]), media_type="image/png")

    return app
