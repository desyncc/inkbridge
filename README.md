# 🌲 Viwoods Companion for Obsidian

**Viwoods Companion** is a dedicated synchronization and handwriting OCR pipeline that connects your **Viwoods AiPaper** e-ink tablet with your **Obsidian Vault**.

It mirrors your tablet's folder hierarchy, downloads high-resolution ink scans, transcribes handwriting using native offline OCR or AI vision models, and non-destructively injects daily notes directly into your Obsidian journal templates.

---

## ✨ Features

- **📂 1:1 Directory Mirroring**: Preserves your tablet's exact folder tree (`Paper`, `Journals`, `Meeting`, `Knowledge Base`, etc.) inside your vault under `Viwoods/`.
- **✍️ Non-Destructive Daily Journal Sync**: Detects dated notebooks (e.g. `2026-09-02`) or items in your `Journals` folder and updates your daily note in `10 - Journals/<Month>/YYYY-MM-DD.md`.
  - Automatically targets `# Transcribed text from AiPaper:`.
  - Never overwrites or touches existing sections (`## 🌅 Landing`, `## 🗓️ Timeline`, `## ✅ Check-ins`, MAGI debriefs, etc.).
  - Includes both high-resolution page image embeds and transcribed text.
- **🔍 Multi-Engine OCR Pipeline**:
  - **Ollama Vision (Recommended Local)**: High-accuracy handwriting transcription using local vision LLMs (e.g., `qwen3-vl:8b-instruct`, `qwen2.5-vl-7b-instruct`) via native `/api/chat`. Zero cloud dependencies and exceptional handwriting recognition.
  - **Google Gemini Vision**: Multimodal cloud transcription via Google AI Studio API key (`gemini-2.0-flash`).
  - **Windows Native OCR**: 100% offline, zero network latency, uses Windows built-in `Windows.Media.Ocr` engine.
  - **Local LM Studio**: OpenAI-compatible vision endpoint support.
  - **Content-Addressable Cache**: SHA-256 image hashes ensure page images are only transcribed once, saving compute and time.
- **🌐 Modern Web Dashboard**:
  - Browse your cloud folders and notebooks interactively.
  - Side-by-side split view comparing high-res handwritten ink with the extracted markdown transcript.
  - Trigger full or journal syncs with real-time progress indicators.
  - Configure OCR engine and vault paths from the UI.
- **⚡ Background Sync Daemon**: Automatically runs incremental syncs at customizable intervals (e.g., every 30 minutes).

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+ (Tested with Python 3.12 on Windows)
- Dependencies:
  ```bash
  pip install -r requirements.txt
  ```

### 2. Configuration (`.viwoods_config.json`)
A configuration file is automatically managed in the project root:
```json
{
  "auth_token": "YOUR_JWT_TOKEN",
  "device_sn": "S3AA4104M01234",
  "vault_path": "C:\\Users\\You\\Obsidian",
  "vault_mirror_folder": "Viwoods",
  "daily_folder": "10 - Journals",
  "daily_heading": "# Transcribed text from AiPaper:",
  "ocr_engine": "ollama",
  "ollama_url": "http://localhost:11434",
  "ollama_model": "qwen3-vl:8b-instruct",
  "gemini_api_key": "",
  "lmstudio_url": "http://localhost:1234/v1"
}
```

> **Note on Auth Token**: You can copy your token from `cloud.viwoods.com` in your browser's DevTools (`localStorage.getItem('token')` or Network tab request headers).

---

## 💻 Usage & Commands

Run commands using Python (or `& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"`):

### Launch Web Dashboard
```bash
python companion.py serve
```
Opens the web companion at `http://localhost:8765` in your default browser.

### Check Status & Device Connection
```bash
python companion.py status
```
Verifies cloud credentials, reports device serial number, and lists cloud categories and item counts.

### Change OCR Engine
```bash
# Switch to local Ollama (e.g. qwen3-vl:8b-instruct)
python companion.py set-engine ollama --model qwen3-vl:8b-instruct

# Switch to Windows Native OCR
python companion.py set-engine windows

# Switch to Gemini Vision
python companion.py set-engine gemini
```

### Sync Daily Journals Only
```bash
# Sync last 7 days using active engine (Ollama)
python companion.py journals --days 7

# Force re-transcribe existing journals with Ollama
python companion.py journals --days 7 --force --engine ollama
```
Scans the `Journals` folder for entries and updates corresponding daily notes in `10 - Journals/`.

### Run Full Sync
```bash
python companion.py sync
```
Recursively mirrors all cloud categories (`Paper`, `Meeting`, `Knowledge Base`, `Memo`) into `Viwoods/` in your Obsidian vault. Use `--force` to re-download and re-transcribe existing notes.

### Run Background Daemon
```bash
python companion.py daemon --interval 30
```
Keeps running in the background and checks for new or modified notebooks every 30 minutes.

---

## 📁 Obsidian Vault Organization

When synced, your Obsidian vault receives:

```
C:\Users\You\Obsidian\
├── 10 - Journals\
│   └── September\
│       └── 2026-09-02.md
│           ├── ## 🌅 Landing
│           ├── ## 🗓️ Timeline
│           ├── # Transcribed text from AiPaper:   <-- INJECTED HERE
│           │   ├── ![[2026-09-02_p1.png]]
│           │   └── > NERV GOD'S IN HIS HEAVEN...
│           └── ## ✅ Check-ins
│
└── Viwoods\                                        <-- 1:1 CLOUD MIRROR
    ├── _attachments\                               <-- Saved page PNGs
    │   └── 2026-09-02_0d7e25d2_p1.png
    ├── Paper\
    │   ├── Journals\
    │   │   ├── 2026-09-01.md
    │   │   └── 2026-09-02.md
    │   └── Ideas.md
    └── Meeting\
        └── TeamSync.md
```

---

## 🛠️ Architecture & Under the Hood

- **Viwoods API**: Implements the proprietary MD5 request signing protocol (`uri` parameter + alphabetical key sorting + secret salt + MD5 hex) with `Machine-Model: web` authentication.
- **Decompression**: Automatically decodes base64-encoded GZIP payloads (`paperSync/get`) to retrieve vector stroke metadata and CloudFront page render URLs.
- **Native Windows OCR**: Uses PowerShell WinRT `Windows.Media.Ocr.OcrEngine` to access Windows 10/11's built-in handwriting recognition without requiring Tesseract or cloud APIs.
