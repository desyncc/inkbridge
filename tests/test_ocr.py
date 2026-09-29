"""OCREngine.transcribe: failures raise instead of looking like blank pages."""

import subprocess

import httpx
import pytest

from viwoods import ocr as ocr_module
from viwoods.ocr import OCRConfigurationError, OCREngine, OCRError


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "page.png"
    # Distinct bytes per test, so the shared OCR cache never already has it.
    path.write_bytes(str(tmp_path).encode())
    return str(path)


@pytest.fixture
def ocr(config):
    return OCREngine(config)


def test_engine_failure_raises_and_is_not_cached(ocr, image, monkeypatch):
    def down(*args, **kwargs):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(ocr, "_transcribe_ollama", down)

    with pytest.raises(OCRError, match="connection refused"):
        ocr.transcribe(image)
    assert ocr.cached_transcript(image) is None


def test_blank_page_returns_empty_and_is_cached(ocr, image, monkeypatch):
    monkeypatch.setattr(ocr, "_transcribe_ollama", lambda path, context="": "")

    assert ocr.transcribe(image) == ""
    assert ocr.cached_transcript(image) == ""


def test_unknown_engine_is_a_configuration_error(ocr, image):
    ocr.config.ocr_engine = "tesseract"
    with pytest.raises(OCRConfigurationError, match="Unknown OCR engine"):
        ocr.transcribe(image)


def test_gemini_without_a_key_is_a_configuration_error(ocr, image):
    ocr.config.ocr_engine = "gemini"
    ocr.config.gemini_api_key = ""
    with pytest.raises(OCRConfigurationError, match="no API key"):
        ocr.transcribe(image)


def test_gemini_key_is_sent_as_a_header_not_in_the_url(ocr, image, monkeypatch):
    ocr.config.ocr_engine = "gemini"
    ocr.config.gemini_api_key = "AIzaSECRET"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(400, json={"error": "bad"})

    real_client = httpx.Client
    monkeypatch.setattr(
        ocr_module.httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )

    with pytest.raises(OCRError) as excinfo:
        ocr.transcribe(image)

    assert seen[0].headers["x-goog-api-key"] == "AIzaSECRET"
    assert "AIzaSECRET" not in str(seen[0].url)
    # The error text (printed, and shown in the dashboard) must not leak it.
    assert "AIzaSECRET" not in str(excinfo.value)


# --- Windows -----------------------------------------------------------------

def test_a_failed_engine_on_windows_does_not_fall_back_to_windows_ocr(ocr, image, monkeypatch):
    monkeypatch.setattr(ocr_module.sys, "platform", "win32")

    def down(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    windows_calls = []
    monkeypatch.setattr(ocr, "_transcribe_ollama", down)
    monkeypatch.setattr(ocr, "_transcribe_windows", lambda path: windows_calls.append(path) or "")

    with pytest.raises(OCRError, match="connection refused"):
        ocr.transcribe(image)
    assert windows_calls == []  # Windows OCR didn't stand in for the chosen engine
    assert ocr.cached_transcript(image) is None


def test_a_failed_powershell_run_is_an_error_not_a_blank_page(ocr, image, monkeypatch):
    monkeypatch.setattr(ocr_module.sys, "platform", "win32")
    ocr.config.ocr_engine = "windows"
    monkeypatch.setattr(
        ocr_module.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="", stderr="Exception calling \"GetFileFromPathAsync\"\nAccess denied"),
    )

    with pytest.raises(OCRError, match="exited with code 1: Access denied"):
        ocr.transcribe(image)
    assert ocr.cached_transcript(image) is None


def test_a_successful_powershell_run_returns_its_text(ocr, image, monkeypatch):
    monkeypatch.setattr(ocr_module.sys, "platform", "win32")
    ocr.config.ocr_engine = "windows"
    monkeypatch.setattr(
        ocr_module.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="hello\n", stderr=""),
    )

    assert ocr.transcribe(image) == "hello"
