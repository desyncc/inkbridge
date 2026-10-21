"""
InkBridge - Main CLI & Service Launcher
Sync your notebooks and daily handwriting from cloud.viwoods.com to Obsidian.
"""

import argparse
import getpass
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def missing_dependency_message(error: ImportError, executable: str, script_dir: Path) -> str:
    """
    Explains a ModuleNotFoundError at startup, which nearly always means the wrong Python: the
    system one instead of the project's virtual environment.
    """
    lines = [f"InkBridge needs the Python packages listed in requirements.txt, and this Python "
             f"({executable}) doesn't have {error.name or 'one of them'}."]
    venv_python = script_dir / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_python.exists():
        lines.append(f"This folder has a virtual environment. Run InkBridge with it instead:\n"
                     f"    {venv_python} {Path(sys.argv[0]).name} {' '.join(sys.argv[1:])}".rstrip())
    else:
        lines.append("Install them (inside a virtual environment is best):\n"
                     "    python -m pip install -r requirements.txt")
    lines.append("Or install InkBridge with pipx, which sets this up for you:  pipx install .")
    return "\n".join(lines)


try:
    from viwoods import doctor, health
    from viwoods.config import NO_TOKEN_MESSAGE, ConfigError, load_config, save_config
    from viwoods.client import AuthError, ViwoodsClient
    from viwoods.ocr import OCREngine
    from viwoods.paths import command
    from viwoods.vault import ObsidianVault, VaultNotFoundError
    from viwoods.sync import SyncEngine
except ModuleNotFoundError as _error:
    sys.exit("\n[ERROR] " + missing_dependency_message(_error, sys.executable, Path(__file__).resolve().parent) + "\n")


def cmd_status():
    cfg = load_config()
    client = ViwoodsClient(cfg)
    print("=" * 55)
    print("           INKBRIDGE STATUS")
    print("=" * 55)
    print(f"Vault Directory:     {cfg.vault_path}")
    print(f"Mirrored Folder:     {cfg.vault_mirror_folder}/")
    daily = ObsidianVault(cfg).describe_daily_notes()
    if daily["source"] == "obsidian":
        print(f"Daily Notes:         {daily['folder'] or '(vault root)'}/{daily['format']}.md "
              f"(from Obsidian's Daily notes settings)")
        if daily["template"]:
            print(f"Daily Template:      {daily['template']}")
    else:
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
    print(f"Last sync:           {health.describe_health(health.load_health())}")
    print(f"Alerts:              {'on (notify_url set)' if cfg.notify_url else 'off (set notify_url to be told when a sync needs you)'}")
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


def cmd_login(email: str = None, use_token: bool = False):
    """
    Stores a Viwoods session token. By default signs in with an email and
    password; with `use_token`, takes a token pasted from the browser instead.
    The password is read without echo, never passed as an argument (it would
    land in shell history) and never saved; the token is never printed.
    """
    cfg = load_config()
    client = ViwoodsClient(cfg)

    if use_token:
        token = getpass.getpass("Paste your Viwoods token (input is hidden): ").strip()
        if not token:
            _login_failed("No token entered.")
        cfg.token = token
        try:
            devices = client.get_devices()   # fails with AuthError if the token is no good
        except Exception as e:
            _login_failed(f"That token was not accepted: {e}")
    else:
        email = (email or input("Viwoods email: ")).strip()
        password = getpass.getpass("Viwoods password (input is hidden): ")
        if not email or not password:
            _login_failed("An email and a password are both needed.")
        try:
            client.login(email, password)
        except Exception as e:
            _login_failed(f"Sign-in failed: {e}")
        try:
            devices = client.get_devices()
        except Exception:
            devices = None   # signed in, but couldn't list devices; the token is still good

    save_config(cfg)
    if devices:
        d = devices[0]
        print(f"Signed in. Connected to {d.get('modelName')} (SN: {d.get('machineNumber')}).")
    elif devices is None:
        print(f"Signed in. (Could not list your tablets just now; run `{command('status')}` to check.)")
    else:
        print("Signed in, but no tablet is registered to this account yet.")
    print(f"Your token is saved. Next: set your vault path, then `{command('sync --dry-run')}`.")


def _login_failed(message: str):
    print(f"[ERROR] {message}", file=sys.stderr)
    sys.exit(1)


def cmd_set_engine(engine: str, model: str = None, url: str = None, think: bool = None):
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
    if think is not None and cfg.ocr_engine == "ollama":
        cfg.ollama_think = think
    save_config(cfg)
    print(f"OCR engine updated to: {cfg.ocr_engine}")
    if cfg.ocr_engine == "ollama":
        print(f"  Model:    {cfg.ollama_model}")
        print(f"  URL:      {cfg.ollama_url}")
        print(f"  Thinking: {cfg.ollama_think}")


def cmd_sync(force=False, engine=None, dry_run=False, confirm_deletions=False):
    cfg = load_config()
    if engine:
        cfg.ocr_engine = engine
    eng = SyncEngine(cfg)
    action = "Dry Run" if dry_run else "Starting Full Sync"
    print(f"\n[InkBridge] {action} into Obsidian ({cfg.vault_path})...")
    print(f"Using OCR engine: {cfg.ocr_engine}")
    if dry_run:
        print("Note: Dry run - nothing will be downloaded, transcribed or written")
    elif force:
        print("Note: Force mode enabled (re-downloading and re-transcribing all pages)")

    def on_progress(msg, pct):
        print(f"[{int(pct * 100):3d}%] {msg}")

    result = eng.sync_all(force=force, progress_cb=on_progress, dry_run=dry_run,
                          confirm_deletions=confirm_deletions)
    if not dry_run:
        # Recorded for `status`; no alert, since you are watching this run.
        health.report_success(cfg, result, alert=False)
    print("\n" + "=" * 45)
    if dry_run:
        planned = result.get("planned", [])
        print(f"Dry run: {len(planned)} notebook(s) would be synced, moved or removed.")
        for note in planned:
            print(f"  - [{note['reason']}] {note['folder']}/{note['name']}")
        _print_held_back(result.get("held_back", []))
    else:
        print(f"Sync Complete! {result['total_synced']} notebook(s) updated.")
        for cat, count in result.get("details", {}).items():
            print(f"  - {cat}: {count} note(s)")
        removed = result.get("removed", [])
        if removed:
            print(f"{len(removed)} notebook(s) deleted on the device were removed from the vault:")
            for note in removed:
                print(f"  - {note['folder']}/{note['name']}")
        _print_held_back(result.get("held_back", []))
        _print_incomplete(result.get("incomplete", []))
        _print_skipped_dates(result.get("skipped_dates", []))
    print("=" * 45 + "\n")


def _print_held_back(held):
    if held:
        print(f"\n!! {len(held)} notebook(s) no longer on the device were NOT removed from the vault.")
        print("   That is more than max_deletions_per_sync allows in one sync (or most of your")
        print("   library), which usually means a wrong account or a bad response, not real deletions:")
        for note in held:
            print(f"     - {note['folder']}/{note['name']}")
        print(f"   If they really are deleted, run:  {command('sync --confirm-deletions')}")


def _print_incomplete(names):
    if names:
        print(f"{len(names)} note(s) had pages that failed to download or transcribe "
              f"and will be retried on the next sync:")
        for name in names:
            print(f"  - {name}")


def _print_skipped_dates(dates):
    if dates:
        span = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"
        print(f"{len(dates)} date(s) ({span}) had no daily note to fill in. Create those "
              f"notes in Obsidian, or set create_missing_daily_notes to true.")


def cmd_journals(days=14, force=False, engine=None):
    cfg = load_config()
    if engine:
        cfg.ocr_engine = engine
    eng = SyncEngine(cfg)
    print(f"\n[InkBridge] Syncing Daily Journals...")
    print(f"Using OCR engine: {cfg.ocr_engine}")

    def on_progress(msg, pct):
        print(f"[{int(pct * 100):3d}%] {msg}")

    count = eng.sync_recent_journals(days_back=days, force=force, progress_cb=on_progress)
    print(f"\nCompleted! {count} journal note(s) updated in vault.")
    _print_incomplete(eng.incomplete)
    _print_skipped_dates(sorted(eng.vault.skipped_dates))
    print()


def cmd_daily(days=None, force=False, engine=None):
    cfg = load_config()
    if engine:
        cfg.ocr_engine = engine
    eng = SyncEngine(cfg)
    print(f"\n[InkBridge] Syncing Viwoods Daily app entries...")
    print(f"Using OCR engine: {cfg.ocr_engine}")

    def on_progress(msg, pct):
        print(f"[{int(pct * 100):3d}%] {msg}")

    count = eng.sync_daily_app(days_back=days, force=force, progress_cb=on_progress)
    print(f"\nCompleted! {count} Daily app entry/entries updated in vault.")
    _print_incomplete(eng.incomplete)
    _print_skipped_dates(sorted(eng.vault.skipped_dates))
    print()


def _open_browser_when_ready(host, port, url, timeout=20.0):
    """Waits for the port to accept connections, then opens the browser."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.2)
    print(f"Dashboard did not start listening within {timeout:.0f}s; open {url} manually.")


def cmd_serve(port=8765, host="127.0.0.1", open_browser=True):
    import uvicorn
    from viwoods.server import app

    url = f"http://{host}:{port}"
    print(f"\n=======================================================")
    print(f"  InkBridge Web Dashboard running at:")
    print(f"  -> {url}")
    print(f"=======================================================\n")

    if open_browser:
        # Opening before uvicorn is listening lands on a connection error.
        threading.Thread(
            target=_open_browser_when_ready,
            args=(host, port, url),
            daemon=True
        ).start()

    # Loopback only: the dashboard exposes the Viwoods token and vault paths.
    uvicorn.run(app, host=host, port=port, log_level="info")


# After a rejected sign-in, look again this soon: the user may have run `login` meanwhile.
AUTH_RETRY_MINUTES = 30


def _daemon_cycle(engine, interval, failures, max_backoff_minutes=240):
    """
    One scheduled sync. Returns (minutes to wait, consecutive failures so far).
    """
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running scheduled sync...")
    try:
        # The daemon runs for weeks: pick up a token saved since it started (by
        # `login`, or by a renewal in another process) and the alert address.
        fresh = load_config()
        engine.config.token = fresh.token
        engine.config.notify_url = fresh.notify_url
        try:
            engine.client.refresh_if_expiring()
        except Exception:
            pass
        result = engine.sync_all(force=False, progress_cb=lambda msg, pct: print(f"  {msg}"))
        health.report_success(engine.config, result)
        return interval, 0
    except Exception as e:
        # A dropped connection or an expired token must not kill the loop; back
        # off so a persistent outage isn't hammered.
        failures += 1
        wait_minutes = min(interval * (2 ** (failures - 1)), max_backoff_minutes)
        if isinstance(e, AuthError):
            wait_minutes = min(wait_minutes, AUTH_RETRY_MINUTES)
        print(f"[ERROR] Sync failed ({failures} in a row): {e}")
        traceback.print_exc()
        health.report_failure(engine.config, e)
        return wait_minutes, failures


def cmd_doctor():
    """Checks the install end to end and says how to fix what's wrong."""
    checks = doctor.run_checks()
    print(doctor.render(checks))
    if doctor.has_failures(checks):
        sys.exit(1)


def cmd_test_notify():
    """Sends a test message to notify_url, so you know alerts will reach you."""
    cfg = load_config()
    if not cfg.notify_url:
        print("[ERROR] notify_url is not set. Put an ntfy topic URL (https://ntfy.sh/your-secret-topic) "
              "or a Discord/Slack webhook URL in the Settings tab or in config.json.", file=sys.stderr)
        sys.exit(1)
    if health.send_notification(cfg.notify_url, "InkBridge: test",
                                "If you can read this, InkBridge alerts reach you.", good=True):
        print("Sent. Check your phone or channel.")
    else:
        print("[ERROR] The test message was not delivered; see the line above.", file=sys.stderr)
        sys.exit(1)


def cmd_daemon(interval=30, max_backoff_minutes=240):
    cfg = load_config()
    engine = SyncEngine(cfg)
    engine.ocr.hold_for_gpu = True      # gpu_busy_limit: wait for a quiet GPU before model calls
    print(f"\n[InkBridge] Starting background daemon (sync every {interval} minutes)...")
    print("Press Ctrl+C to stop.\n")

    consecutive_failures = 0

    try:
        while True:
            wait_minutes, consecutive_failures = _daemon_cycle(
                engine, interval, consecutive_failures, max_backoff_minutes)
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
        print(f"Usage: {command('export')} \"<Title>\" [--format pdf|html|zip] [--output <dir>]\n")
        return

    fmt = fmt.lower()
    print(f"\n[InkBridge] Exporting '{query}' as {fmt.upper()}...")
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
    parser = argparse.ArgumentParser(description="InkBridge: sync Viwoods AiPaper into Obsidian")
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
    p_sync.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything")
    p_sync.add_argument("--engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine to use")
    p_sync.add_argument("--confirm-deletions", action="store_true",
                        help="Remove notebooks deleted on the device even if there are more than max_deletions_per_sync")

    # journals
    p_journals = subparsers.add_parser("journals", help="Sync daily journals only")
    p_journals.add_argument("--days", type=int, default=14, help="Days back to scan")
    p_journals.add_argument("--force", action="store_true", help="Force re-sync")
    p_journals.add_argument("--engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine to use")

    # daily
    p_daily = subparsers.add_parser("daily", help="Sync Viwoods Daily app pages and to-dos only")
    p_daily.add_argument("--days", type=int, default=None, help="Days back to scan (default: config daily_app_days_back)")
    p_daily.add_argument("--force", action="store_true", help="Force re-sync")
    p_daily.add_argument("--engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine to use")

    # set-engine
    p_engine = subparsers.add_parser("set-engine", help="Change default OCR engine")
    p_engine.add_argument("engine", choices=["windows", "ollama", "gemini", "lmstudio"], help="OCR engine name")
    p_engine.add_argument("--model", type=str, help="Model name (e.g. qwen3.5:9b)")
    p_engine.add_argument("--url", type=str, help="API URL (e.g. http://localhost:11434)")
    think_group = p_engine.add_mutually_exclusive_group()
    think_group.add_argument("--think", dest="think", action="store_true", default=None, help="Enable Ollama model thinking (ollama engine only)")
    think_group.add_argument("--no-think", dest="think", action="store_false", help="Disable Ollama model thinking")

    # login
    p_login = subparsers.add_parser("login", help="Sign in to Viwoods and store your session token")
    p_login.add_argument("--email", help="Your Viwoods account email (asked for if omitted)")
    p_login.add_argument("--token", dest="use_token", action="store_true",
                         help="Paste a token copied from the browser instead of signing in with a password")

    # doctor
    subparsers.add_parser("doctor", help="Check this install and say how to fix any problem")

    # test-notify
    subparsers.add_parser("test-notify", help="Send a test alert to notify_url")

    # status
    subparsers.add_parser("status", help="Print current status and connection info")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run scheduled sync service in background")
    p_daemon.add_argument("--interval", type=int, default=30, help="Interval in minutes (default: 30)")

    args = parser.parse_args()

    try:
        _dispatch(args)
    except (ConfigError, VaultNotFoundError) as e:
        sys.stdout.flush()  # keep any banner already printed above the error
        print(f"\n[ERROR] {e}\n", file=sys.stderr)
        sys.exit(1)


def _dispatch(args):
    if args.command in ("serve", "gui") or args.command is None:
        cmd_serve(port=getattr(args, "port", 8765), open_browser=not getattr(args, "no_browser", False))
    elif args.command == "export":
        cmd_export(query=args.query, fmt=args.format, output_dir=args.output, list_notes=args.list)
    elif args.command == "sync":
        cmd_sync(force=args.force, engine=getattr(args, "engine", None), dry_run=args.dry_run,
                 confirm_deletions=args.confirm_deletions)
    elif args.command == "journals":
        cmd_journals(days=args.days, force=args.force, engine=getattr(args, "engine", None))
    elif args.command == "daily":
        cmd_daily(days=args.days, force=args.force, engine=getattr(args, "engine", None))
    elif args.command == "set-engine":
        cmd_set_engine(args.engine, model=getattr(args, "model", None), url=getattr(args, "url", None), think=getattr(args, "think", None))
    elif args.command == "login":
        cmd_login(email=args.email, use_token=args.use_token)
    elif args.command == "doctor":
        cmd_doctor()
    elif args.command == "test-notify":
        cmd_test_notify()
    elif args.command == "status":
        cmd_status()
    elif args.command == "daemon":
        cmd_daemon(interval=args.interval)


if __name__ == "__main__":
    main()
