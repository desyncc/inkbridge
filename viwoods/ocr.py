import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
import httpx

from .config import Config

CACHE_FILE = Path(__file__).resolve().parent.parent / ".viwoods_ocr_cache.json"


class OCREngine:
    def __init__(self, config: Config):
        self.config = config
        self.cache = self._load_cache()

    def _load_cache(self) -> dict:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def get_image_hash(self, image_path: str) -> str:
        h = hashlib.sha256()
        with open(image_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def transcribe(self, image_path: str, context_prompt: str = "") -> str:
        """Transcribes a handwritten notebook page image to Markdown text."""
        if not os.path.exists(image_path):
            return ""

        img_hash = self.get_image_hash(image_path)
        cache_key = f"{self.config.ocr_engine}:{img_hash}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        engine = self.config.ocr_engine.lower()
        result = ""

        try:
            if engine == "gemini" and self.config.gemini_api_key:
                result = self._transcribe_gemini(image_path, context_prompt)
            elif engine == "ollama":
                result = self._transcribe_ollama(image_path, context_prompt)
            elif engine == "lmstudio":
                result = self._transcribe_lmstudio(image_path, context_prompt)
            elif sys.platform == "win32":
                result = self._transcribe_windows(image_path)
            else:
                print(f"Warning: Engine '{engine}' not supported or missing credentials on Linux. Please use 'ollama' or 'gemini'.")
                result = ""
        except Exception as e:
            if sys.platform == "win32":
                print(f"Warning: OCR engine '{engine}' failed ({e}), falling back to Windows Native OCR...")
                try:
                    result = self._transcribe_windows(image_path)
                except Exception as e2:
                    print(f"Error: Windows OCR fallback also failed: {e2}")
                    result = ""
            else:
                print(f"Error: OCR engine '{engine}' failed ({e}). Ensure Ollama or your vision service is running.")
                result = ""

        if result:
            self.cache[cache_key] = result
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
        """Transcribes image using local Ollama Vision API (e.g. qwen3.5:9b or qwen3-vl)."""
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
