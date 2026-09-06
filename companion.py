"""
Viwoods Companion - Main CLI & Service Launcher
Sync your notebooks and daily handwriting from cloud.viwoods.com to Obsidian.
"""

import argparse
import sys
import time
import traceback
import webbrowser
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from viwoods.config import NO_TOKEN_MESSAGE, load_config, save_config
from viwoods.client import ViwoodsClient
from viwoods.ocr import OCREngine
from viwoods.vault import ObsidianVault
from viwoods.sync import SyncEngine


def cmd_status():
    cfg = load_config()
    client = ViwoodsClient(cfg)
    print("=" * 55)
    print("           VIWOODS COMPANION STATUS")
    print("=" * 55)
    print(f"Vault Directory:     {cfg.vault_path}")
    print(f"Mirrored Folder:     {cfg.vault_mirror_folder}/")
    print(f"Daily Folder:        {cfg.daily_folder}/")
    print(f"Active OCR Engine:   {cfg.ocr_engine}")
    if cfg.ocr_engine == "ollama":
        print(f"Ollama URL:          {cfg.ollama_url}")
        print(f"Ollama Model:        {cfg.ollama_model}")
        print(f"Ollama Thinking:     {getattr(cfg, 'ollama_think', False)}")
    elif cfg.ocr_engine == "lmstudio":
        print(f"LM Studio URL:       {cfg.lmstudio_url}")
        print(f"LM Studio Model:     {cfg.lmstudio_model}")
    elif cfg.ocr_engine == "gemini":
        print(f"Gemini Model:        {cfg.gemini_model}")
    print("-" * 55)

    if not cfg.token:
        print(f"Viwoods Cloud:       NOT CONFIGURED\n{NO_TOKEN_MESSAGE}")
        print("=" * 55)
        return

    try:
        devices = client.get_devices()
        print("Viwoods Cloud:       CONNECTED")
        for d in devices:
            print(f"Device:              {d.get('modelName')} (SN: {d.get('machineNumber')})")
        
        roots = client.get_root_folders()
        print("Cloud Categories:")
        for r in roots:
            if r.get("count", 0) > 0:
                print(f"  - {r.get('name')}: {r.get('count')} items")
    except Exception as e:
        print(f"Viwoods Cloud:       ERROR ({e})")
    print("=" * 55)


def cmd_set_engine(engine: str, model: str = None, url: str = None):
    cfg = load_config()
    cfg.ocr_engine = engine.lower()
    if model:
        if cfg.ocr_engine == "ollama":
            cfg.ollama_model = model
        elif cfg.ocr_engine == "lmstudio":
            cfg.lmstudio_model = model
        elif cfg.ocr_engine == "gemini":
            cfg.gemini_model = model
    if url:
        if cfg.ocr_engine == "ollama":
            cfg.ollama_url = url
        elif cfg.ocr_engine == "lmstudio":
            cfg.lmstudio_url = url
    save_config(cfg)
    print(f"OCR engine updated to: {cfg.ocr_engine}")
    if cfg.ocr_engine == "ollama":
        print(f"  Model: {cfg.ollama_model}")
        print(f"  URL:   {cfg.ollama_url}")


def cmd_sync(force=False, engine=None):
    cfg = load_config()
    if engine:
        cfg.ocr_engine = engine
    eng = SyncEngine(cfg)
    print(f"\n[Viwoods Companion] Starting Full Sync into Obsidian ({cfg.vault_path})...")
    print(f"Using OCR engine: {cfg.ocr_engine}")
    if force:
        print("Note: Force mode enabled (re-downloading and re-transcribing all pages)")

    def on_progress(msg, pct):
        print(f"[{int(pct * 100):3d}%] {msg}")

    result = eng.sync_all(force=force, progress_cb=on_progress)
    print("\n" + "=" * 45)
    print(f"Sync Complete! {result['total_synced']} notebook(s) updated.")
    for cat, count in result.get("details", {}).items():
        print(f"  - {cat}: {count} note(s)")
    print("=" * 45 + "\n")


def cmd_journals(days=14, force=False, engine=None):
    cfg = load_config()
    if engine:
        cfg.ocr_engine = engine
    eng = SyncEngine(cfg)
    print(f"\n[Viwoods Companion] Syncing Daily Journals...")
    print(f"Using OCR engine: {cfg.ocr_engine}")

    def on_progress(msg, pct):
        print(f"[{int(pct * 100):3d}%] {msg}")

    count = eng.sync_recent_journals(days_back=days, force=force, progress_cb=on_progress)
    print(f"\nCompleted! {count} journal note(s) updated in vault.\n")


def cmd_serve(port=8765, host="127.0.0.1", open_browser=True):
    import uvicorn
    from viwoods.server import app

    url = f"http://{host}:{port}"
    print(f"\n=======================================================")
    print(f"  Viwoods Companion Web Dashboard running at:")
    print(f"  -> {url}")
    print(f"=======================================================\n")

    if open_browser:
        webbrowser.open(url)

    # Loopback only: the dashboard exposes the Viwoods token and vault paths.
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_daemon(interval=30, max_backoff_minutes=240):
    cfg = load_config()
    engine = SyncEngine(cfg)
    print(f"\n[Viwoods Companion] Starting background daemon (sync every {interval} minutes)...")
    print("Press Ctrl+C to stop.\n")

    consecutive_failures = 0

    try:
        while True:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running scheduled sync...")
            try:
                engine.sync_all(force=False, progress_cb=lambda msg, pct: print(f"  {msg}"))
                consecutive_failures = 0
                wait_minutes = interval
            except Exception as e:
                # A dropped connection or an expired token must not kill the
                # loop; back off so a persistent outage isn't hammered.
                consecutive_failures += 1
                wait_minutes = min(interval * (2 ** (consecutive_failures - 1)), max_backoff_minutes)
                print(f"[ERROR] Sync failed ({consecutive_failures} in a row): {e}")
                traceback.print_exc()

            print(f"Sleeping for {wait_minutes} minutes...\n")
            time.sleep(wait_minutes * 60)
    except KeyboardInterrupt:
        print("\nDaemon stopped.")


def cmd_export(query=None, fmt="pdf", output_dir=None, list_notes=False):
    from viwoods.exporter import NoteExporter
    exp = NoteExporter()

    if list_notes or not query:
        notes = exp.list_notes()
        print("\n" + "=" * 65)
        print(f"       AVAILABLE NOTEBOOKS FOR EXPORT ({len(notes)} notes)")
        print("=" * 65)
        current_cat = None
        for n in notes:
            cat = n.get("category", "General")
            if cat != current_cat:
                current_cat = cat
                print(f"\n[{cat}]")
            uuid_short = f" ({n['uuid'][:8]}...)" if n.get("uuid") else ""
            print(f"  - {n['title']}{uuid_short}")
        print("\n" + "=" * 65)
        print("Usage: python companion.py export \"<Title>\" [--format pdf|html|zip] [--output <dir>]\n")
        return

    fmt = fmt.lower()
    print(f"\n[Viwoods Companion] Exporting '{query}' as {fmt.upper()}...")
    try:
        if fmt == "html":
            out_file = exp.export_html(query, output_dir=output_dir)
        elif fmt == "zip":
            out_file = exp.export_zip(query, output_dir=output_dir)
        else:
            out_file = exp.export_pdf(query, output_dir=output_dir)

        print(f"[OK] Successfully exported to:\n  {out_file} ({out_file.stat().st_size:,} bytes)\n")
    except Exception as e:
        print(f"[ERROR] Export failed: {e}\n")


def main():
    parser = argparse.ArgumentParser(description="Viwoods Companion for Obsidian")
    subparsers = parser.add_subparsers(dest="command")

    # serve / gui
    p_serve = subparsers.add_parser("serve", help="Launch the Web Companion dashboard")
    p_serve.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    p_serve.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")

    p_gui = subparsers.add_parser("gui", help="Launch the Web Companion dashboard (alias for serve)")
    p_gui.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    p_gui.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")

    # export
    p_export = subparsers.add_parser("export", help="Export a note to PDF, standalone HTML, or ZIP")
    p_export.add_argument("query", nargs="?", default=None, help="Notebook title or UUID to export")
    p_export.add_argument("--format", choices=["pdf", "html", "zip"], default="pdf", help="Export format (default: pdf)")
    p_export.add_argument("--output", "-o", type=str, default=None, help="Custom output directory")
    p_export.add_argument("--list", "-l", action="store_true", help="List all available notes")

    # sync
    p_sync = subparsers.add_parser("sync", help="Run full sync of all notebooks and daily notes")
    p_sync.add_argument("--force", action="store_true", help="Force re-sync and re-transcribe all notes")
    p_sync.add_argument("--engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine to use")

    # journals
    p_journals = subparsers.add_parser("journals", help="Sync daily journals only")
    p_journals.add_argument("--days", type=int, default=14, help="Days back to scan")
    p_journals.add_argument("--force", action="store_true", help="Force re-sync")
    p_journals.add_argument("--engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine to use")

    # set-engine
    p_engine = subparsers.add_parser("set-engine", help="Change default OCR engine")
    p_engine.add_argument("engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine name")
    p_engine.add_argument("--model", type=str, help="Model name (e.g. qwen3.5:9b)")
    p_engine.add_argument("--url", type=str, help="API URL (e.g. http://localhost:11434)")

    # status
    subparsers.add_parser("status", help="Print current status and connection info")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run scheduled sync service in background")
    p_daemon.add_argument("--interval", type=int, default=30, help="Interval in minutes (default: 30)")

    args = parser.parse_args()

    if args.command in ("serve", "gui") or args.command is None:
        cmd_serve(port=getattr(args, "port", 8765), open_browser=not getattr(args, "no_browser", False))
    elif args.command == "export":
        cmd_export(query=args.query, fmt=args.format, output_dir=args.output, list_notes=args.list)
    elif args.command == "sync":
        cmd_sync(force=args.force, engine=getattr(args, "engine", None))
    elif args.command == "journals":
        cmd_journals(days=args.days, force=args.force, engine=getattr(args, "engine", None))
    elif args.command == "set-engine":
        cmd_set_engine(args.engine, model=getattr(args, "model", None), url=getattr(args, "url", None))
    elif args.command == "status":
        cmd_status()
    elif args.command == "daemon":
        cmd_daemon(interval=args.interval)


if __name__ == "__main__":
    main()
