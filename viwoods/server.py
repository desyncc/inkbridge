import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .client import AuthError, ViwoodsClient
from .config import Config, load_config, save_config
from .ocr import OCREngine
from .sync import LOCAL_CACHE_DIR, SyncEngine
from .vault import ObsidianVault

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Runs the auto-sync scheduler for as long as the dashboard is up."""
    thread = threading.Thread(target=_auto_sync_loop, name="viwoods-auto-sync", daemon=True)
    thread.start()
    try:
        yield
    finally:
        auto_sync_stop.set()


app = FastAPI(title="Viwoods Companion API", version="1.0.0", lifespan=lifespan)

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

# On-demand page transcriptions, keyed "<uuid>:<pageNo>".
transcribe_jobs: Dict[str, Dict[str, Any]] = {}
transcribe_lock = threading.Lock()

# Config.auto_sync_interval scheduler (serve mode). The config is re-read every
# tick, so changing the interval in Settings takes effect without a restart.
AUTO_SYNC_TICK_SECONDS = 30
auto_sync_stop = threading.Event()
auto_sync_state: Dict[str, Any] = {"interval": 0, "last_run": None, "next_run": None}


def _auto_sync_loop() -> None:
    next_run: Optional[float] = None
    last_interval = None

    while not auto_sync_stop.is_set():
        try:
            interval = int(getattr(load_config(), "auto_sync_interval", 0) or 0)
        except Exception:
            interval = 0

        if interval != last_interval:
            next_run = None
            last_interval = interval
        auto_sync_state["interval"] = interval

        if interval <= 0:
            next_run = None
            auto_sync_state["next_run"] = None
        elif next_run is None:
            next_run = time.monotonic() + interval * 60
            auto_sync_state["next_run"] = (
                datetime.now() + timedelta(minutes=interval)
            ).isoformat(timespec="seconds")
        elif time.monotonic() >= next_run:
            if _claim_sync_slot("Automatic sync starting..."):
                print(f"[auto-sync] Running scheduled sync (every {interval} min)")
                run_sync_task("all", force=False)
                auto_sync_state["last_run"] = datetime.now().isoformat(timespec="seconds")
            else:
                print("[auto-sync] Skipped: a sync is already running")
            next_run = time.monotonic() + interval * 60
            auto_sync_state["next_run"] = (
                datetime.now() + timedelta(minutes=interval)
            ).isoformat(timespec="seconds")

        auto_sync_stop.wait(AUTO_SYNC_TICK_SECONDS)


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
    create_missing_daily_notes: Optional[bool] = None
    ocr_engine: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    lmstudio_url: Optional[str] = None
    lmstudio_model: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_think: Optional[bool] = None
    token: Optional[str] = None
    auto_sync_interval: Optional[int] = None
    download_recordings: Optional[bool] = None
    max_pages_per_notebook: Optional[int] = None
    infer_tags: Optional[bool] = None
    max_inferred_tags: Optional[int] = None
    daily_app_days_back: Optional[int] = None


class LoginRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None


class SyncRequest(BaseModel):
    scope: str = "all"  # "all", "journals", "daily", "paper", or "folder" with resource_id
    force: bool = False
    resource_id: Optional[str] = ""
    app_type: int = 1


@app.get("/api/status")
def get_status():
    cfg = load_config()
    engine = get_engine()
    devices = []
    connected = False
    error = None
    auth_error = False

    try:
        devices = engine.client.get_devices()
        connected = True
    except AuthError as e:
        # Distinguished from a generic failure so the UI can point at Settings.
        error = str(e)
        auth_error = True
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
        "error": error,
        "auth_error": auth_error,
        "has_token": bool(cfg.token)
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


def _page_cache_path(uuid: str, page_no: Any) -> Path:
    return LOCAL_CACHE_DIR / f"{uuid}_p{page_no}.png"


@app.get("/api/preview/{uuid}")
def get_note_preview(uuid: str, app_type: int = 1):
    """
    Returns the note's pages with any transcript already available. This never
    runs OCR: a vision model can take minutes per page and would hold the
    request (and the browser) open the whole time. Use POST
    /api/transcribe/{uuid}/{page_no} to transcribe on demand.
    """
    engine = get_engine()
    try:
        detail = engine.client.get_paper_detail(uuid, app_type=app_type)
        image_pages = detail.get("imagePages", [])
        pages = []
        for idx, page in enumerate(image_pages, start=1):
            page_no = page.get("pageNo", idx)
            img_url = page.get("imageUrl") or page.get("pageImageUrl")
            direct_content = (page.get("content") or "").strip()

            local_cache_img = _page_cache_path(uuid, page_no)
            transcript = direct_content
            if not transcript and local_cache_img.exists():
                cached = engine.ocr.cached_transcript(str(local_cache_img))
                transcript = cached or ""

            pages.append({
                "pageNo": page_no,
                "imageUrl": img_url,
                "cached": local_cache_img.exists(),
                "transcript": transcript,
                # True when nothing is available yet and OCR has to be asked for.
                "needs_transcription": not transcript
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


def _run_transcription(uuid: str, page_no: int, app_type: int, force: bool) -> None:
    """Downloads the page if needed and runs OCR. Executed off the request."""
    key = f"{uuid}:{page_no}"
    try:
        engine = get_engine()
        detail = engine.client.get_paper_detail(uuid, app_type=app_type)

        page = next(
            (p for p in detail.get("imagePages", [])
             if str(p.get("pageNo")) == str(page_no)),
            None
        )
        if page is None:
            raise ValueError(f"Page {page_no} not found in note {uuid}")

        img_path = _page_cache_path(uuid, page_no)
        if not img_path.exists() or force:
            img_url = page.get("imageUrl") or page.get("pageImageUrl")
            if not img_url:
                raise ValueError(f"Page {page_no} has no image to transcribe")
            engine.client.download_file(img_url, str(img_path))

        transcript = engine.ocr.transcribe(
            str(img_path),
            context_prompt=f"Notebook: {detail.get('name', '')}",
            force=force
        )
        engine.ocr.flush_cache()
        with transcribe_lock:
            transcribe_jobs[key] = {"state": "done", "transcript": transcript, "error": None}
    except Exception as e:
        with transcribe_lock:
            transcribe_jobs[key] = {"state": "error", "transcript": None, "error": str(e)}


@app.post("/api/transcribe/{uuid}/{page_no}")
def transcribe_page(
    uuid: str,
    page_no: int,
    background_tasks: BackgroundTasks,
    app_type: int = 1,
    force: bool = False
):
    """Explicitly requests OCR for one page; poll the GET for the result."""
    key = f"{uuid}:{page_no}"
    with transcribe_lock:
        job = transcribe_jobs.get(key)
        if job and job.get("state") == "running":
            return {"code": 200, "uuid": uuid, "pageNo": page_no, **job}
        transcribe_jobs[key] = {"state": "running", "transcript": None, "error": None}

    background_tasks.add_task(_run_transcription, uuid, page_no, app_type, force)
    return {"code": 202, "uuid": uuid, "pageNo": page_no, "state": "running"}


@app.get("/api/transcribe/{uuid}/{page_no}")
def get_transcription_status(uuid: str, page_no: int):
    key = f"{uuid}:{page_no}"
    with transcribe_lock:
        job = transcribe_jobs.get(key)
    if not job:
        return {"code": 200, "uuid": uuid, "pageNo": page_no, "state": "idle",
                "transcript": None, "error": None}
    return {"code": 200, "uuid": uuid, "pageNo": page_no, **job}


def _claim_sync_slot(message: str = "Initializing sync...") -> bool:
    """Marks a sync as running. Returns False if one already is."""
    with sync_lock:
        if sync_status["is_running"]:
            return False
        sync_status["is_running"] = True
        sync_status["progress"] = 0.0
        sync_status["message"] = message
        sync_status["error"] = None
        sync_status["last_result"] = None
    return True


def run_sync_task(scope: str, force: bool, resource_id: str = "", app_type: int = 1) -> None:
    """Runs a sync and publishes its progress. The slot must already be claimed."""
    engine = get_engine()

    def on_progress(msg: str, pct: float):
        sync_status["message"] = msg
        sync_status["progress"] = pct

    try:
        if scope == "journals":
            count = engine.sync_recent_journals(force=force, progress_cb=on_progress)
            result = {"scope": "journals", "synced": count}
        elif scope == "daily":
            count = engine.sync_daily_app(force=force, progress_cb=on_progress)
            result = {"scope": "daily", "synced": count}
        elif resource_id:
            count = engine.sync_resource(
                app_type=app_type, resource_id=resource_id,
                force=force, progress_cb=on_progress
            )
            result = {"scope": "folder", "resource_id": resource_id,
                      "app_type": app_type, "synced": count}
        elif scope == "paper":
            result = engine.sync_all(force=force, progress_cb=on_progress, app_types=[1])
            result["scope"] = "paper"
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


@app.post("/api/sync")
def trigger_sync(req: SyncRequest, background_tasks: BackgroundTasks):
    if not _claim_sync_slot():
        # A real 409, not a 200 whose body says 409.
        return JSONResponse(
            status_code=409,
            content={"code": 409, "message": "Sync is already running.", "status": sync_status}
        )

    background_tasks.add_task(
        run_sync_task, req.scope, req.force, req.resource_id or "", req.app_type
    )
    return {"code": 200, "message": "Sync started.", "status": sync_status}


@app.get("/api/sync/status")
def get_sync_status():
    return {"code": 200, "status": sync_status, "auto_sync": auto_sync_state}


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
