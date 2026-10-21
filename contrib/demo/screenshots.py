"""
Regenerates the README's screenshots (docs/images/*.png) from the demo.

    pip install playwright          # drives a browser you already have; downloads nothing else
    python contrib/demo/screenshots.py [--chrome /path/to/chrome-or-chromium]

It starts run_demo.py on a spare port with a throwaway vault, so the pictures
show the made-up demo library and never anyone's real notebooks.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CHROME_NAMES = ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "chrome"]


def find_chrome(given):
    if given:
        return given
    return next((p for p in map(shutil.which, CHROME_NAMES) if p), None)


def wait_for(url, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"The demo didn't come up at {url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chrome", help="Chrome or Chromium executable (found automatically if on PATH)")
    parser.add_argument("--out", default=str(ROOT / "docs" / "images"))
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--cloud-port", type=int, default=8778)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="inkbridge-shots-"))
    demo = subprocess.Popen(
        [sys.executable, str(HERE / "run_demo.py"), "--workdir", str(work),
         "--port", str(args.port), "--cloud-port", str(args.cloud_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{args.port}"
        wait_for(f"{base}/api/sync/status")

        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=find_chrome(args.chrome), headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
            page.goto(base)
            page.wait_for_load_state("networkidle")

            # A notebook: the ink beside its transcript
            page.click('[data-tab="tabStudio"]')
            page.get_by_text("Garden plan").first.click()
            page.wait_for_function(
                "() => { const i = document.querySelector('#studioSplitView img'); return i && i.complete && i.naturalWidth > 0; }"
            )
            page.wait_for_timeout(500)
            page.screenshot(path=str(out / "ocr-studio.png"))

            browser.close()
        print(f"Wrote screenshots to {out}")
    finally:
        demo.terminate()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
