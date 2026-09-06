# 🌲 Viwoods Companion for Obsidian

**Viwoods Companion** is a dedicated synchronization and handwriting OCR pipeline that connects your **Viwoods AiPaper** e-ink tablet with your **Obsidian Vault**.

It mirrors your tablet's folder hierarchy, downloads high-resolution ink scans, transcribes handwriting using native offline OCR or AI vision models, and injects daily notes into a clearly delimited block inside your Obsidian journal notes.

---

## ✨ Features

- **📂 1:1 Directory Mirroring**: Preserves your tablet's exact folder tree (`Paper`, `Journals`, `Meeting`, `Knowledge Base`, etc.) inside your vault under `Viwoods/`.
- **✍️ Marker-Delimited Daily Journal Sync**: Detects dated notebooks (e.g. `2026-09-02`) or items in your `Journals` folder and updates your daily note in `10 - Journals/<Month>/YYYY-MM-DD.md`.
  - Targets the heading configured as `daily_heading` (default `# Transcribed text from AiPaper:`), matched on an exact line.
  - Writes only between `<!-- viwoods:start -->` and `<!-- viwoods:end -->`. Everything outside those markers — `## 🌅 Landing`, `## 🗓️ Timeline`, `## ✅ Check-ins`, anything else — is left untouched.
  - Each notebook gets its own `<!-- viwoods:note <uuid> -->` sub-block, so several notebooks can feed the same date and each is updated independently.
  - A note that has the heading but no markers (written by an older version) is migrated on the next sync: the markers are inserted after the heading and the block ends at the next heading of any level or the next `---`.
  - Includes both high-resolution page image embeds and transcribed text.
- **🔍 Multi-Engine OCR Pipeline**:
  - **Ollama Vision (default)**: local vision LLMs (e.g. `qwen3.5:9b`, `qwen2.5-vl-7b-instruct`) via native `/api/chat`. Zero cloud dependencies.
  - **Google Gemini Vision**: multimodal cloud transcription via a Google AI Studio API key (`gemini-2.0-flash`).
  - **Local LM Studio**: any OpenAI-compatible vision endpoint.
  - **Windows Native OCR**: 100% offline via `Windows.Media.Ocr`. **Windows only** — on Linux/macOS the app reports it and transcribes nothing, so pick another engine there.
  - **Content-Addressable Cache**: keyed `engine:model:sha256`, so a page is transcribed once per model and switching models re-transcribes rather than serving a stale result.
- **🌐 Modern Web Dashboard** (loopback only, `127.0.0.1`):
  - Browse your cloud folders and notebooks interactively.
  - Side-by-side split view comparing high-res handwritten ink with the extracted markdown transcript.
  - Transcription is on demand: opening a note never blocks on OCR — press **Transcribe Page** for a page you want text for.
  - Trigger full or journal syncs with real-time progress indicators.
  - Configure OCR engine, vault paths, sync interval and the page cap from the UI.
- **⚡ Background Sync**: `companion.py daemon` for a standalone service (with backoff on repeated failures), or set `auto_sync_interval` to have the dashboard sync on a schedule while it runs.
- **📤 Export**: any note to PDF, standalone HTML or a ZIP bundle, from the CLI or the dashboard.

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- Dependencies:
  ```bash
  pip install -r requirements.txt
  ```

### 2. Configuration (`~/.viwoods/config.json`)

Everything the app stores lives in a per-user data directory, not in the
project folder:

| Path | Contents |
| --- | --- |
| `~/.viwoods/config.json` | Settings, including your Viwoods token and Gemini key |
| `~/.viwoods/sync_state.json` | What has been synced, per note |
| `~/.viwoods/ocr_cache.json` | Transcripts, keyed by engine, model and image hash |
| `~/.viwoods/cache/` | Downloaded page scans and recordings |

Set `VIWOODS_DATA_DIR` to put them somewhere else:

```bash
VIWOODS_DATA_DIR=~/Sync/viwoods python companion.py sync
```

Files from the old layout (`.viwoods_config.json` and friends in the project
root) are moved into `~/.viwoods` automatically on first run. Setting
`VIWOODS_DATA_DIR` skips that migration, so an explicit directory always
starts from whatever is already in it.

The config file is created on first run. Every key it understands, with its
default:

```json
{
  "token": "",
  "machine_number": "",
  "machine_model": "web",
  "device_name": "AiPaper",
  "api_base_url": "https://api.viwoods.com",
  "secret_key": "O9EfpIx4g9o8TKuCv2n5msBHucSrAf",

  "vault_path": "C:\\Users\\You\\Obsidian",
  "vault_mirror_folder": "Viwoods",
  "vault_attachments_folder": "Viwoods/Attachments",
  "daily_folder": "10 - Journals",
  "daily_heading": "# Transcribed text from AiPaper:",
  "create_missing_daily_notes": true,

  "ocr_engine": "ollama",
  "gemini_api_key": "",
  "gemini_model": "gemini-2.0-flash",
  "lmstudio_url": "http://localhost:1234/v1",
  "lmstudio_model": "qwen2.5-vl-7b-instruct",
  "ollama_url": "http://localhost:11434",
  "ollama_model": "qwen3.5:9b",
  "ollama_think": false,

  "auto_sync_interval": 0,
  "download_recordings": true,
  "max_pages_per_notebook": 50
}
```

| Key | Meaning |
| --- | --- |
| `token` | Your Viwoods `Access-Token` (JWT). Required. |
| `machine_number` | Device serial. Filled in automatically from your first registered device. |
| `machine_model`, `device_name` | Identify this client to the API; leave as-is. |
| `api_base_url` | Viwoods Cloud API root. |
| `secret_key` | Salt for the API's MD5 request signature. |
| `vault_path` | Absolute path to your Obsidian vault. |
| `vault_mirror_folder` | Folder in the vault holding the 1:1 cloud mirror. |
| `vault_attachments_folder` | Where page PNGs and recordings are written. |
| `daily_folder` | Folder containing your daily notes. |
| `daily_heading` | Heading in a daily note under which the marked block is injected. |
| `create_missing_daily_notes` | Create the daily note when it does not exist. Set `false` to let Obsidian's own daily-note template create it first. |
| `ocr_engine` | `ollama`, `lmstudio`, `gemini` or `windows` (Windows only). |
| `gemini_api_key`, `gemini_model` | Google AI Studio credentials and model. |
| `lmstudio_url`, `lmstudio_model` | OpenAI-compatible vision endpoint and model. |
| `ollama_url`, `ollama_model` | Ollama endpoint and vision model. |
| `ollama_think` | Pass `think` to Ollama for reasoning models. |
| `auto_sync_interval` | Minutes between automatic syncs while `serve` is running. `0` disables. |
| `download_recordings` | Save meeting audio into the attachments folder and link it. |
| `max_pages_per_notebook` | Cap on pages per notebook (large imported PDF planners). `0` means no cap; when pages are dropped the mirrored note says so. |

> **Note on the auth token**: copy it from `cloud.viwoods.com` in your browser's DevTools (`localStorage.getItem('token')` or a Network-tab request header), then paste it into the dashboard's Settings tab.

> **These files hold secrets.** `config.json` contains your Viwoods JWT and your Gemini API key; `ocr_cache.json`, `sync_state.json` and `cache/` contain your note contents and page scans. They live outside the repository, and their old project-root names are listed in `.gitignore` — keep them out of version control and out of shared folders.

---

## 💻 Usage & Commands

### Launch Web Dashboard
```bash
python companion.py serve          # or: python companion.py gui
```
Opens the web companion at `http://127.0.0.1:8765` in your default browser. It
binds loopback only — the dashboard can read your vault and holds your token.
Use `--port` to change the port and `--no-browser` to skip opening a browser.

### Check Status & Device Connection
```bash
python companion.py status
```
Verifies cloud credentials, reports device serial number, and lists cloud categories and item counts.

### Change OCR Engine
```bash
# Local Ollama (default)
python companion.py set-engine ollama --model qwen3.5:9b

# Gemini Vision
python companion.py set-engine gemini

# Windows Native OCR (Windows only)
python companion.py set-engine windows
```

### Sync Daily Journals Only
```bash
# Sync last 7 days using the active engine
python companion.py journals --days 7

# Force re-transcribe existing journals with Ollama
python companion.py journals --days 7 --force --engine ollama
```
Finds your `Journals` folder in Paper and updates the matching daily notes in `10 - Journals/`.

### Run Full Sync
```bash
python companion.py sync
```
Recursively mirrors all cloud categories (`Paper`, `Meeting`, `Learning`, `Knowledge Base`, `Memo`) into `Viwoods/` in your Obsidian vault. Use `--force` to re-download and re-transcribe existing notes.

```bash
python companion.py sync --dry-run
```
Lists the notebooks that would be synced, and why, without fetching, transcribing or writing anything.

### Export a Note
```bash
# List everything available to export
python companion.py export --list

# By title (substring is enough) or by UUID
python companion.py export "Morning Pages" --format pdf
python companion.py export "Morning Pages" --format html --output ~/Desktop
python companion.py export 0d7e25d2-4f39-4122-affa-ea02704a5637 --format zip
```
`--format` is `pdf` (default), `html` (standalone, images inlined as base64) or `zip` (markdown plus attachments). `--output`/`-o` picks the directory; the default is `exports/`.

### Run Background Daemon
```bash
python companion.py daemon --interval 30
```
Checks for new or modified notebooks every 30 minutes. A failed sync is logged and retried with exponential backoff instead of killing the daemon.

---

## 📁 Obsidian Vault Organization

When synced, your Obsidian vault receives:

```
<vault>/
├── 10 - Journals/
│   └── September/
│       └── 2026-09-02.md
│           ├── ## 🌅 Landing
│           ├── ## 🗓️ Timeline
│           ├── # Transcribed text from AiPaper:
│           │   ├── <!-- viwoods:start -->          <-- INJECTED BLOCK
│           │   ├──   <!-- viwoods:note 0d7e25d2… -->
│           │   ├──   ![[Viwoods/Attachments/2026-09-02_0d7e25d2_p1.png]]
│           │   ├──   > NERV GOD'S IN HIS HEAVEN...
│           │   ├──   <!-- viwoods:note-end 0d7e25d2… -->
│           │   └── <!-- viwoods:end -->
│           └── ## ✅ Check-ins                      <-- never touched
│
└── Viwoods/                                        <-- 1:1 CLOUD MIRROR
    ├── Attachments/                                <-- page PNGs & recordings
    │   └── 2026-09-02_0d7e25d2_p1.png
    ├── Paper/
    │   ├── Journals/
    │   │   ├── 2026-09-01.md
    │   │   └── 2026-09-02.md
    │   └── Ideas.md
    └── Meeting/
        └── TeamSync.md
```

Attachment names are `<title>_<uuid8>_p<n>.png`; the title is truncated first so the uuid, page number and extension always survive.

---

## 🛠️ Architecture & Under the Hood

- **Viwoods API**: Implements the proprietary MD5 request signing protocol (`uri` parameter + alphabetical key sorting + secret salt + MD5 hex) with `Machine-Model: web` authentication. Values are serialized as JSON so booleans and nested objects sign the way the server reads them. Folder listings are paginated until exhausted.
- **Decompression**: Automatically decodes base64-encoded GZIP payloads (`paperSync/get`) to retrieve vector stroke metadata and CloudFront page render URLs.
- **Native Windows OCR**: Uses PowerShell WinRT `Windows.Media.Ocr.OcrEngine` to access Windows 10/11's built-in handwriting recognition without requiring Tesseract or cloud APIs.
- **Concurrency**: the sync state and OCR cache are written under a cross-process file lock and merged, so a CLI sync and the dashboard can run at the same time without clobbering each other.

---

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite covers the daily-journal marker injection (including legacy
migration, multiple notebooks per date and CRLF files), attachment naming,
note lookup, the markdown-to-HTML conversion, request signing and folder
pagination. It runs against a temporary `VIWOODS_DATA_DIR`, so it never
touches your real config, state or cache.
