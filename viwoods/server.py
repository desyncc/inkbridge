import asyncio
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .client import ViwoodsClient
from .config import Config, load_config, save_config
from .ocr import OCREngine
from .sync import SyncEngine
from .vault import ObsidianVault

app = FastAPI(title="Viwoods Companion API", version="1.0.0")

# No CORS middleware on purpose: the dashboard is served from this same
# origin, and the API holds the Viwoods token and the Gemini key.

WEB_DIR = Path(__file__).resolve().parent / "web"

# Shared global state
sync_status = {
    "is_running": False,
    "progress": 0.0,
    "message": "Idle",
    "last_result": None,
    "error": None
}

sync_lock = threading.Lock()


def get_engine() -> SyncEngine:
    cfg = load_config()
    client = ViwoodsClient(cfg)
    ocr = OCREngine(cfg)
    vault = ObsidianVault(cfg)
    return SyncEngine(cfg, client=client, ocr=ocr, vault=vault)


class ConfigUpdateRequest(BaseModel):
    vault_path: Optional[str] = None
    vault_mirror_folder: Optional[str] = None
    daily_folder: Optional[str] = None
    daily_heading: Optional[str] = None
    mirror_daily: Optional[bool] = None
    ocr_engine: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    lmstudio_url: Optional[str] = None
    lmstudio_model: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    token: Optional[str] = None
    auto_sync_interval: Optional[int] = None


class LoginRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None


class SyncRequest(BaseModel):
    scope: str = "all"  # "all", "journals", "paper"
    force: bool = False
    resource_id: Optional[str] = ""


@app.get("/api/status")
def get_status():
    cfg = load_config()
    engine = get_engine()
    devices = []
    connected = False
    error = None

    try:
        devices = engine.client.get_devices()
        connected = True
    except Exception as e:
        error = str(e)

    vault_exists = os.path.isdir(cfg.vault_path)
    notes_synced = len(engine.state.get("notes", {}))

    return {
        "connected": connected,
        "device": {
            "model": cfg.device_name or "AiPaper",
            "sn": cfg.machine_number,
            "all_devices": devices
        },
        "vault": {
            "path": cfg.vault_path,
            "exists": vault_exists,
            "mirror_folder": cfg.vault_mirror_folder,
            "daily_folder": cfg.daily_folder,
            "notes_synced": notes_synced
        },
        "ocr": {
            "engine": cfg.ocr_engine,
            "has_gemini_key": bool(cfg.gemini_api_key),
            "lmstudio_url": cfg.lmstudio_url,
            "ollama_url": cfg.ollama_url,
            "ollama_model": cfg.ollama_model
        },
        "last_sync": engine.state.get("last_full_sync"),
        "error": error
    }


@app.get("/api/tree")
def get_tree():
    """Returns the mirrored category and folder tree from Viwoods Cloud."""
    engine = get_engine()
    try:
        root_folders = engine.client.get_root_folders()
        tree = []
        for rf in root_folders:
            app_type = rf.get("appType", 1)
            name = rf.get("name", "Unknown")
            count = rf.get("count", 0)

            items = []
            if count > 0:
                try:
                    items = engine.client.get_folder_items(app_type=app_type, resource_id="")
                except Exception as e:
                    print(f"Failed to fetch items for {name}: {e}")

            tree.append({
                "appType": app_type,
                "name": name,
                "count": count,
                "items": items
            })
        return {"code": 200, "tree": tree}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/folder/{app_type}")
def get_folder_contents(app_type: int, resource_id: str = ""):
    engine = get_engine()
    try:
        items = engine.client.get_folder_items(app_type=app_type, resource_id=resource_id)
        return {"code": 200, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/journals")
def get_journal_items():
    """
    Lists the entries in the user's Journals folder, discovered the same way
    SyncEngine.sync_recent_journals() discovers it.
    """
    engine = get_engine()
    try:
        folder = engine.find_journal_folder()
        if not folder:
            return {"code": 200, "folder": None, "items": []}

        folder_uuid = folder.get("uuid") or folder.get("resourceId") or ""
        return {
            "code": 200,
            "folder": {"uuid": folder_uuid, "name": folder.get("name")},
            "items": engine.client.get_folder_items(app_type=1, resource_id=folder_uuid)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/notes")
def get_synced_notes():
    engine = get_engine()
    return {
        "code": 200,
        "notes": engine.state.get("notes", {}),
        "last_full_sync": engine.state.get("last_full_sync")
    }


@app.get("/api/preview/{uuid}")
def get_note_preview(uuid: str, app_type: int = 1):
    engine = get_engine()
    try:
        detail = engine.client.get_paper_detail(uuid, app_type=app_type)
        image_pages = detail.get("imagePages", [])
        pages = []
        for idx, page in enumerate(image_pages, start=1):
            page_no = page.get("pageNo", idx)
            img_url = page.get("imageUrl") or page.get("pageImageUrl")
            direct_content = page.get("content", "").strip()

            # Check if cached locally
            local_cache_img = Path(__file__).resolve().parent.parent / ".viwoods_cache" / f"{uuid}_p{page_no}.png"
            transcript = direct_content
            if not transcript and local_cache_img.exists():
                transcript = engine.ocr.transcribe(str(local_cache_img))

            pages.append({
                "pageNo": page_no,
                "imageUrl": img_url,
                "cached": local_cache_img.exists(),
                "transcript": transcript
            })

        return {
            "code": 200,
            "uuid": uuid,
            "appType": app_type,
            "name": detail.get("name"),
            "pages": pages,
            "recordings": detail.get("recordings", []),
            "lastModifiedTime": detail.get("lastModifiedTime")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sync")
def trigger_sync(req: SyncRequest, background_tasks: BackgroundTasks):
    global sync_status
    with sync_lock:
        if sync_status["is_running"]:
            return {"code": 409, "message": "Sync is already running.", "status": sync_status}

        sync_status["is_running"] = True
        sync_status["progress"] = 0.0
        sync_status["message"] = "Initializing sync..."
        sync_status["error"] = None
        sync_status["last_result"] = None

    def run_sync_task(scope: str, force: bool, resource_id: str):
        global sync_status
        engine = get_engine()

        def on_progress(msg: str, pct: float):
            sync_status["message"] = msg
            sync_status["progress"] = pct

        try:
            if scope == "journals":
                count = engine.sync_recent_journals(force=force, progress_cb=on_progress)
                result = {"scope": "journals", "synced": count}
            else:
                result = engine.sync_all(force=force, progress_cb=on_progress)

            sync_status["last_result"] = result
            sync_status["message"] = "Completed"
            sync_status["progress"] = 1.0
        except Exception as e:
            sync_status["error"] = str(e)
            sync_status["message"] = f"Failed: {e}"
        finally:
            sync_status["is_running"] = False

    background_tasks.add_task(run_sync_task, req.scope, req.force, req.resource_id)
    return {"code": 200, "message": "Sync started.", "status": sync_status}


@app.get("/api/sync/status")
def get_sync_status():
    return {"code": 200, "status": sync_status}


@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    cfg = load_config()
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    save_config(cfg)
    return {"code": 200, "message": "Configuration saved.", "config": get_config()["config"]}


SECRET_FIELDS = ("token", "gemini_api_key")


def _mask_secret(value: Optional[str]) -> str:
    """Returns a display-only tail, e.g. '…_MKpDY', never the secret itself."""
    value = (value or "").strip()
    if not value:
        return ""
    return "…" + value[-6:] if len(value) > 6 else "…"


@app.get("/api/config")
def get_config():
    cfg = load_config()
    data = cfg.model_dump()

    # Secrets never leave the process: the UI gets a has_* flag and a tail.
    for field in SECRET_FIELDS:
        data[f"has_{field}"] = bool((data.get(field) or "").strip())
        data[f"{field}_masked"] = _mask_secret(data.get(field))
        data.pop(field, None)

    return {"code": 200, "config": data}


@app.post("/api/login")
def login(req: LoginRequest):
    cfg = load_config()
    if req.token:
        cfg.token = req.token.strip()
        save_config(cfg)
        return {"code": 200, "message": "Token updated successfully."}

    if not req.email or not req.password:
        raise HTTPException(status_code=400, detail="Must provide email & password or token.")

    client = ViwoodsClient(cfg)
    try:
        client.login(req.email, req.password)
        save_config(cfg)
        return {"code": 200, "message": "Logged in successfully."}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.get("/api/export/{uuid}")
def export_note_endpoint(uuid: str, format: str = "pdf", app_type: Optional[int] = None):
    """Exports a note to PDF, Standalone HTML, or ZIP and returns it as a file download."""
    from .exporter import NoteExporter
    exporter = NoteExporter()
    try:
        fmt = format.lower()
        if fmt == "html":
            out_file = exporter.export_html(uuid, app_type=app_type)
            media_type = "text/html"
        elif fmt == "zip":
            out_file = exporter.export_zip(uuid, app_type=app_type)
            media_type = "application/zip"
        else:
            out_file = exporter.export_pdf(uuid, app_type=app_type)
            media_type = "application/pdf"

        return FileResponse(
            path=str(out_file),
            filename=out_file.name,
            media_type=media_type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Static Web UI Hosting
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def serve_ui():
        return FileResponse(str(WEB_DIR / "index.html"))
