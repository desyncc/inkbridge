"""The dashboard's Transcribe Page button works on the page as it is now."""

from viwoods import server


class Engine:
    def __init__(self, ink):
        self.ink = ink
        self.client = self
        self.ocr = self

    # client
    def get_paper_detail(self, uuid, app_type=1):
        return {"name": "Ideas", "imagePages": [{"pageNo": 1, "imageUrl": "https://x/p1.png"}]}

    def download_file(self, url, target_path):
        with open(target_path, "wb") as f:
            f.write(self.ink)

    # ocr
    def transcribe(self, path, context_prompt="", force=False):
        with open(path, "rb") as f:
            return f.read().decode()

    def flush_cache(self):
        pass


def test_transcribe_page_fetches_the_current_page_not_a_cached_copy(monkeypatch):
    uuid = "server-transcribe-test"
    cached = server._page_cache_path(uuid, 1)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"stale ink from an old sync")
    monkeypatch.setattr(server, "get_engine", lambda: Engine(b"fresh ink"))

    server._run_transcription(uuid, 1, app_type=1, force=False)

    assert server.transcribe_jobs[f"{uuid}:1"]["transcript"] == "fresh ink"
