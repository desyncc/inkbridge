"""
Run InkBridge's dashboard against a pretend Viwoods Cloud and a throwaway vault.

    python contrib/demo/run_demo.py

Nothing real is touched: the config, sync state and vault live in a temporary
folder, and the "cloud" is contrib/demo/fake_viwoods.py. Open the URL it prints.
This is how the README's screenshots are made (see screenshots.py).
"""

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def serve_in_background(app, port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765, help="dashboard port (default 8765)")
    parser.add_argument("--cloud-port", type=int, default=8799, help="fake cloud port (default 8799)")
    parser.add_argument("--workdir", help="keep everything here instead of a temporary folder")
    parser.add_argument("--vault-only", action="store_true",
                        help="build the demo vault, then exit without starting the dashboard")
    args = parser.parse_args()

    work = Path(args.workdir or tempfile.mkdtemp(prefix="inkbridge-demo-"))
    vault = work / "vault"
    # Must be set before anything from viwoods is imported: paths are fixed at import.
    os.environ["VIWOODS_DATA_DIR"] = str(work / "data")

    import demo_vault
    import fake_viwoods
    import uvicorn
    from viwoods.config import Config, save_config
    from viwoods.sync import SyncEngine

    demo_vault.seed_vault(vault)   # the notes someone would already have
    cloud_url = f"http://127.0.0.1:{args.cloud_port}"
    cfg = Config(
        token="demo-token",
        api_base_url=cloud_url,
        vault_path=str(vault),
        obsidian_daily_notes=True,   # use the vault's own Daily notes settings, as in real use
        machine_model="AiPaper",     # what the tablet reports itself as, shown in each note's properties
    )
    save_config(cfg)
    serve_in_background(fake_viwoods.build_app(cloud_url), args.cloud_port)

    print("Syncing the demo library into the demo vault...")
    result = SyncEngine(cfg).sync_all()
    print(f"{result['total_synced']} demo notebooks synced into {vault}")
    if args.vault_only:
        return

    print(f"\nDashboard: http://127.0.0.1:{args.port}   (Ctrl+C to stop)\n")
    from viwoods.server import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
