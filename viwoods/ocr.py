import base64
import hashlib
import os
import re
import subprocess
import sys
from typing import Optional
import httpx

from .config import Config
from .jsonstore import read_json, update_json
from .paths import data_path

CACHE_FILE = data_path("ocr_cache.json")

# Transcripts held in memory before the cache file is rewritten. A notebook is
# flushed as soon as it finishes, so at most this many can be lost on a crash.
CACHE_FLUSH_EVERY = 10


class OCRConfigurationError(RuntimeError):
    """The selected OCR engine cannot run as configured on this machine."""


class OCREngine:
    def __init__(self, config: Config):
        self.config = config
        self.cache = self._load_cache()
        # Keys transcribed by this process, merged into the file on each save.
        self._pending: set = set()
        self._warned: set = set()

    def _warn_once(self, key: str, message: str) -> None:
        """Prints a configuration error once per process, not once per page."""
        if key not in self._warned:
            self._warned.add(key)
            print(f"Error: {message}")

    def _active_model(self, engine: str) -> str:
        """The model identifying this engine's output, for cache keying."""
        return {
            "ollama": self.config.ollama_model,
            "lmstudio": self.config.lmstudio_model,
            "gemini": self.config.gemini_model,
        }.get(engine, "native") or "default"

    def _load_cache(self) -> dict:
        return read_json(CACHE_FILE, {})

    def flush_cache(self) -> None:
        """Writes any transcripts still held in memory. Call at a safe point."""
        self._save_cache(force=True)

    def _save_cache(self, force: bool = False):
        """
        Merges the transcripts produced by this process into the file on disk,
        so a parallel sync's results are not thrown away.

        Writes are batched: the cache file holds every transcript ever made,
        so rewriting it after each page turns a long sync into O(pages²) of
        JSON serialization.
        """
        if not force and len(self._pending) < CACHE_FLUSH_EVERY:
            return

        pending = {key: self.cache[key] for key in self._pending if key in self.cache}
        if not pending:
            return

        def mutate(disk_cache: dict) -> None:
            disk_cache.update(pending)

        try:
            self.cache = update_json(CACHE_FILE, mutate, {})
            self._pending.clear()
        except Exception as e:
            print(f"Warning: Failed to save OCR cache: {e}")

    def get_image_hash(self, image_path: str) -> str:
        h = hashlib.sha256()
        with open(image_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def cache_key(self, image_path: str) -> str:
        """
        Cache key for this image under the *current* engine and model, so
        switching models does not serve a transcript produced by the old one.
        """
        engine = (self.config.ocr_engine or "").lower()
        return f"{engine}:{self._active_model(engine)}:{self.get_image_hash(image_path)}"

    def cached_transcript(self, image_path: str) -> Optional[str]:
        """Returns the cached transcript ("" is a real result), or None if absent."""
        if not os.path.exists(image_path):
            return None
        return self.cache.get(self.cache_key(image_path))

    def transcribe(self, image_path: str, context_prompt: str = "", force: bool = False) -> str:
        """Transcribes a handwritten notebook page image to Markdown text."""
        if not os.path.exists(image_path):
            return ""

        engine = (self.config.ocr_engine or "").lower()
        key = self.cache_key(image_path)

        # A blank page transcribes to "" — a real result worth caching, so it
        # is not re-OCR'd on every sync. --force bypasses the cache entirely.
        if not force and key in self.cache:
            return self.cache[key] or ""

        result = ""
        engine_ran = False

        try:
            if engine == "gemini":
                if not (self.config.gemini_api_key or "").strip():
                    self._warn_once(
                        "gemini-no-key",
                        "Gemini selected but no API key configured. Add one in "
                        "Settings, or switch the OCR engine (e.g. ollama)."
                    )
                    return ""
                result = self._transcribe_gemini(image_path, context_prompt)
                engine_ran = True
            elif engine == "ollama":
                result = self._transcribe_ollama(image_path, context_prompt)
                engine_ran = True
            elif engine == "lmstudio":
                result = self._transcribe_lmstudio(image_path, context_prompt)
                engine_ran = True
            elif engine == "windows":
                if sys.platform != "win32":
                    self._warn_once(
                        "windows-not-available",
                        f"OCR engine 'windows' only runs on Windows (this is "
                        f"{sys.platform}). Switch to 'ollama', 'lmstudio' or "
                        f"'gemini' — no pages will be transcribed until you do."
                    )
                    return ""
                result = self._transcribe_windows(image_path)
                engine_ran = True
            else:
                self._warn_once(
                    f"unknown-engine-{engine}",
                    f"Unknown OCR engine '{engine}'. Valid engines: ollama, "
                    f"lmstudio, gemini, windows."
                )
                return ""
        except Exception as e:
            if sys.platform == "win32" and engine != "windows":
                print(f"Warning: OCR engine '{engine}' failed ({e}), falling back to Windows Native OCR...")
                try:
                    result = self._transcribe_windows(image_path)
                    engine_ran = True
                except Exception as e2:
                    print(f"Error: Windows OCR fallback also failed: {e2}")
                    return ""
            else:
                print(f"Error: OCR engine '{engine}' failed ({e}). Ensure Ollama or your vision service is running.")
                return ""

        if engine_ran:
            self.cache[key] = result
            self._pending.add(key)
            self._save_cache()

        return result

    def _transcribe_windows(self, image_path: str) -> str:
        """Invokes Windows built-in WinRT OCR engine via PowerShell."""
        abs_path = os.path.abspath(image_path).replace("'", "''")
        ps_script = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]

function Await($WinRtTask, $ResultType) {{
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Media, ContentType = WindowsRuntime] | Out-Null

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{abs_path}')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($engine -eq $null) {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output ""
}} else {{
    $ocrResult = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $ocrResult.Text
}}
"""
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        return proc.stdout.strip()

    def _transcribe_gemini(self, image_path: str, context: str = "") -> str:
        """Transcribes image using Google Gemini Vision API."""
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            "Transcribe this handwritten page from a digital e-ink notebook accurately into clean Markdown. "
            "Preserve bullet points, checkboxes, timestamps, math formulas, headers, and paragraph structure. "
            "Do not add commentary, conversational filler, or introductions—output ONLY the transcribed text."
        )
        if context:
            prompt += f"\nContext regarding this note: {context}"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.gemini_model}:generateContent?key={self.config.gemini_api_key}"
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": img_b64
                        }
                    }
                ]
            }]
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts).strip()
        return ""

    def _transcribe_lmstudio(self, image_path: str, context: str = "") -> str:
        """Transcribes image using local LM Studio / OpenAI-compatible Vision API."""
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            "Transcribe this handwritten notebook page accurately into clean Markdown. "
            "Preserve formatting, lists, headings, and timestamps. Output only the transcript."
        )

        url = f"{self.config.lmstudio_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.lmstudio_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                    ]
                }
            ],
            "temperature": 0.2
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
        return ""

    def _transcribe_ollama(self, image_path: str, context: str = "") -> str:
        """Transcribes image using local Ollama Vision API (e.g. qwen3.5:9b)."""
        try:
            from PIL import Image
            import io
            im = Image.open(image_path)
            # Composite transparent background onto white to avoid solid black background
            if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
                im = im.convert('RGBA')
                bg = Image.new('RGB', im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[3])
                im = bg
            else:
                im = im.convert('RGB')

            if max(im.size) > 1600:
                im.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=92)
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception:
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            "Transcribe this handwritten page from a digital e-ink notebook accurately into clean Markdown. "
            "Preserve lists, bullet points, checkboxes, headings, dates, and paragraph structure. "
            "Do not add commentary, conversational filler, or introductions—output ONLY the transcribed text."
        )
        if context:
            prompt += f"\nContext regarding this note: {context}"

        url = f"{self.config.ollama_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.config.ollama_model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [img_b64]
                }
            ],
            "stream": False,
            "think": getattr(self.config, "ollama_think", False),
            "options": {
                "temperature": 0.1
            }
        }

        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_content = data.get("message", {}).get("content", "").strip()
            # Strip any thinking tags if present
            cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
            return cleaned
