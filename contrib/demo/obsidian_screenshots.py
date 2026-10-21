"""
Screenshots of the demo vault in a real Obsidian (docs/images/obsidian-*.png).

    pip install playwright
    python contrib/demo/obsidian_screenshots.py

Builds the demo vault (run_demo.py --vault-only), then starts Obsidian with a
throwaway profile pointed at it, so your own Obsidian settings and vaults are
never read or changed, and takes the pictures through its debugging port.
On Linux it uses xvfb-run when available, so no window appears on your screen.
Needs Obsidian installed (--obsidian /path/to/it if it isn't on PATH).
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# Opened one after another so the file explorer ends up showing the folders we want.
REVEAL = [
    "99 - Viwoods/Meeting/Team sync 09-04.md",
    "99 - Viwoods/Paper/Reference/Reading list.md",
    "99 - Viwoods/Paper/Projects/Garden plan.md",
]
DAILY = "10 - Journals/11 - Daily/2026-09-02.md"
NOTEBOOK = "99 - Viwoods/Paper/Projects/Garden plan.md"

OPEN_JS = """async (path) => {
  const file = app.vault.getAbstractFileByPath(path);
  const leaf = app.workspace.getLeaf(false);
  await leaf.openFile(file, { state: { mode: 'preview' } });
  app.commands.executeCommandById('file-explorer:reveal-active-file');
}"""

SCROLL_TO_JS = """([text, above]) => {
  const target = [...document.querySelectorAll('.markdown-preview-view h1, .markdown-preview-view h2, .markdown-preview-view h3')]
    .find(h => h.textContent.includes(text));
  if (!target) return;
  target.scrollIntoView({ block: 'start' });
  document.querySelector('.markdown-preview-view').scrollBy(0, -above);   // leave some of what's above in view
}"""


def wait_for_port(port, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
            return
        except OSError:
            time.sleep(1)
    raise SystemExit("Obsidian didn't start (is it installed? try --obsidian /path/to/obsidian)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--obsidian", default="obsidian", help="the Obsidian executable")
    parser.add_argument("--out", default=str(ROOT / "docs" / "images"))
    parser.add_argument("--port", type=int, default=9333, help="debugging port for Obsidian")
    parser.add_argument("--cloud-port", type=int, default=8788)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="inkbridge-obsidian-"))
    subprocess.run(
        [sys.executable, str(HERE / "run_demo.py"), "--workdir", str(work), "--vault-only",
         "--cloud-port", str(args.cloud_port)],
        check=True, stdout=subprocess.DEVNULL,
    )

    profile = work / "profile"
    profile.mkdir()
    (profile / "obsidian.json").write_text(json.dumps({"vaults": {"demo0000000001": {
        "path": str(work / "vault"), "ts": 1790000000000, "open": True}}}), encoding="utf-8")

    command = [args.obsidian, f"--user-data-dir={profile}", f"--remote-debugging-port={args.port}"]
    if sys.platform.startswith("linux") and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", "-s", "-screen 0 1440x900x24"] + command
    app = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True)
    try:
        wait_for_port(args.port)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
            page = browser.contexts[0].pages[0]
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_function("() => window.app && app.workspace && app.workspace.layoutReady")

            for path in REVEAL:
                page.evaluate(OPEN_JS, path)
                page.wait_for_timeout(300)

            # A daily note: the user's own sections, with InkBridge's block between them.
            page.evaluate(OPEN_JS, DAILY)
            page.wait_for_timeout(1200)
            page.evaluate(SCROLL_TO_JS, ["Landing", 20])
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / "obsidian-daily-note.png"))

            # A mirrored notebook: where the handwritten page meets its transcription.
            page.evaluate(OPEN_JS, NOTEBOOK)
            page.wait_for_timeout(1500)
            page.evaluate(SCROLL_TO_JS, ["Transcription", 150])
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / "obsidian-notebook.png"))
            browser.close()
        print(f"Wrote screenshots to {out}")
    finally:
        try:
            os.killpg(os.getpgid(app.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
