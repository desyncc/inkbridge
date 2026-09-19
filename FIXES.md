Fix the following bugs in this InkBridge project (companion.py + viwoods/ package + viwoods/web/). Work through them in order, one commit-sized change at a time. After each fix, run a quick sanity check (python -m py_compile on touched files, and the pytest suite once it exists). Do not touch the slider-captcha login code in client.py beyond leaving it as-is.

## 1. Data-loss and secret-leak fixes (do these first)

1.1 viwoods/vault.py sync_daily_journal(): the injected section is delimited by guessing the "next H1" with the regex `\n(#[^#].*)`. This wipes any `## ...` H2 section that follows the heading, and also mis-treats `#tag` lines as headings. Replace with explicit markers: write the injected block as
    <heading>\n<!-- viwoods:start -->\n...\n<!-- viwoods:end -->
    and on re-sync replace only the text between the markers. If the heading exists but no markers do (legacy notes), insert markers immediately after the heading and stop at the first line that starts with `#` followed by a space at ANY heading level, or at `---`. Also handle the heading appearing more than once (currently re.split drops everything after the second occurrence) and make the heading match exact-line (`^heading\s*$`), not prefix.

1.2 viwoods/vault.py sync_daily_journal(): two notebooks mapped to the same date overwrite each other. Key each injected block by notebook uuid (e.g. `<!-- viwoods:note <uuid> -->` sub-markers inside the section) so multiple notebooks for one date coexist and each is updated independently.

1.3 viwoods/sync.py sync_notebook() line ~126 + viwoods/vault.py save_attachment(): attachment filename is built as `<title>_<uuid8>_p<n>.png` and THEN sanitized/truncated to 60 chars, so long titles drop the uuid, page number and `.png` extension, and all pages collide into one extension-less file. Fix: truncate the title portion first (to ~40 chars), then append `_<uuid8>_p<n>.png`, and make _sanitize_filename never strip a file extension.

1.4 viwoods/config.py: remove DEFAULT_TOKEN (a real personal JWT) and the hardcoded personal machine_number default. Default token should be "" and the app should print/return a clear "no token configured" message. Add a .gitignore containing: .viwoods_config.json, .viwoods_ocr_cache.json, .viwoods_sync_state.json, .viwoods_cache/, exports/, __pycache__/.

1.5 viwoods/server.py: bind uvicorn to 127.0.0.1 (companion.py cmd_serve), remove the CORSMiddleware allow_origins=["*"] block entirely (same-origin UI does not need CORS), and make GET /api/config redact secrets (token, gemini_api_key) e.g. return has_token / has_gemini_key booleans plus a masked tail.

1.6 viwoods/web/app.js saveSettings() + loadStatus(): saving settings currently posts empty/HTML-default values for gemini_api_key, daily_folder, daily_heading, lmstudio_model, mirror_daily, wiping the saved Gemini key. Fix: (a) populate every settings input from GET /api/config on load; (b) only include gemini_api_key and token in the POST if the field is non-empty; (c) send partial updates only for fields the user changed.

## 2. Functional bugs

2.1 viwoods/client.py get_folder_items(): add pagination. Loop pageIndex until fewer than pageSize items come back (or use the total count in the response), and return the full list. All callers in sync.py and server.py should get every item.

2.2 companion.py cmd_daemon(): wrap each sync_all() call in try/except Exception so a network error logs and sleeps instead of killing the loop. Add a simple backoff on repeated failures.

2.3 viwoods/web/app.js + viwoods/server.py: preview/export ignore app type. Include appType in tree item rows (data-apptype), pass it through openInStudio() -> /api/preview/{uuid}?app_type=N, and store app_type in the sync state per note so the Studio sidebar and /api/export can use it. In exporter.find_note(), use the stored app_type before trying all types.

2.4 viwoods/web/app.js loadJournals(): remove the hardcoded folder uuid `0d7e25d2-4f39-4122-affa-ea02704a5637`. Add GET /api/journals to server.py that finds the Journals folder the same way SyncEngine.sync_recent_journals() does and returns its items; have the UI call that.

2.5 viwoods/ocr.py transcribe(): cache key is `engine:hash`. Change to `engine:model:hash` (model = ollama_model / lmstudio_model / gemini_model, "native" for windows) so switching models does not reuse stale transcripts. Keep old keys readable or just let them go stale.

2.6 viwoods/ocr.py transcribe(): empty results are never cached, so blank pages get re-OCR'd every sync. Cache an explicit empty-string result (store "" or a sentinel) and honor it on lookup; --force should bypass the cache.

2.7 viwoods/ocr.py transcribe(): if engine is gemini and gemini_api_key is empty, raise/print "Gemini selected but no API key configured" instead of silently falling through to the Windows branch. On non-Windows, if engine is "windows", print a one-time clear error rather than a warning per page.

2.8 viwoods/exporter.py export_html(): heading/bold regexes run AFTER newlines are replaced with <br/>, so `^# ` only matches the first line and the whole transcript becomes one <h1>. Convert markdown line-by-line first (escape, then per-line heading/bullet/bold handling), then join with <br/> or wrap in <p>.

2.9 viwoods/sync.py sync_notebook(): max_pages_per_notebook truncation is silent and uses API order. Sort image_pages by pageNo before capping, and when pages are dropped, add a line to the mirrored note ("N of M pages synced, cap = max_pages_per_notebook") and store both synced and total counts in state.

2.10 viwoods/vault.py mirror_notebook(): `created` is always today because the detail dict never has createdAt/creationTime. Pass the list item's creation timestamp (check the raw item for createTime/createdAt/create_time keys) from sync_notebook into metadata, and fall back to lastModifiedTime before falling back to now.

2.11 viwoods/sync.py sync_notebook() skip condition requires pages_count > 0, so zero-page notes are re-fetched every sync. Skip whenever last_modified <= cached_mod, regardless of page count.

2.12 viwoods/server.py get_note_preview(): remove the synchronous OCR call inside the GET (it can block for minutes). Return the cached transcript if present, otherwise return transcript "" and a flag; add POST /api/transcribe/{uuid}/{page} that runs OCR in a background task if the user explicitly asks.

2.13 Dead config: either implement or remove mirror_daily (the "Also keep a mirrored copy under Viwoods/Daily/" checkbox), download_recordings (recordings are never downloaded; local_audio_path is never set), auto_sync_interval (serve mode has no scheduler), and SyncRequest.resource_id / scope "paper" (ignored). Preferred: implement auto_sync_interval as a background thread in server.py that calls sync_all when > 0, implement download_recordings by downloading recording file URLs into the attachments folder, and remove mirror_daily.

2.14 Sync state concurrency: CLI and dashboard each load the state file once and rewrite it whole. Re-read the state file before each write and merge the notes dict, or use a file lock, so concurrent runs do not clobber each other. Same for the OCR cache.

2.15 viwoods/web/app.js: initSettingsToggles() and bindEvents() both attach a change handler to #cfgOcrEngine; the first one never toggles #secOllama. Keep one handler that toggles all three sections.

2.16 viwoods/client.py sign_data(): values are stringified with Python f-strings, so booleans/nested values would sign as "True"/"{...}" rather than the JSON the server sees. Serialize non-string values with json.dumps(v, separators=(",",":")) and lowercase booleans. Also drop the redundant second uri assignment (post() already sets it).

2.17 viwoods/client.py post(): on HTTP 401 or a Viwoods "token expired" code, raise a dedicated AuthError; server.py /api/status should surface "token expired, paste a new one in Settings" rather than a generic error, and the UI should link to the Settings tab.

## 3. README corrections

3.1 Config example: keys are `token` and `machine_number` (not auth_token / device_sn). Document all keys actually in Config: secret_key, vault_attachments_folder, daily_folder, daily_heading, ocr_engine, gemini_*, lmstudio_*, ollama_url, ollama_model, ollama_think, auto_sync_interval, download_recordings, max_pages_per_notebook.
3.2 Vault diagram shows Viwoods/_attachments/; code writes Viwoods/Attachments/ (config vault_attachments_folder).
3.3 Document the `export` command (pdf/html/zip, --list, --output) and the `gui` alias.
3.4 Make the default model consistent everywhere (README, config.py, index.html, app.js fallback) — pick one, e.g. qwen3-vl:8b-instruct or qwen3.5:9b.
3.5 Change the default ocr_engine in config.py to "ollama" (the "windows" default cannot work on Linux/macOS) and note that Windows OCR is Windows-only.
3.6 Remove or rewrite the "Never overwrites or touches existing sections" claim to describe the marker-based injection from 1.1.
3.7 Mention that config/cache/state files hold secrets and are gitignored.

## 4. Improvements after the fixes

4.1 Move .viwoods_config.json, .viwoods_ocr_cache.json, .viwoods_sync_state.json and .viwoods_cache/ out of the project root into a per-user data dir (platformdirs or ~/.viwoods/), with an env var override.
4.2 Add tests/ with pytest covering: vault.sync_daily_journal (markers, H2 preservation, two notebooks same date, heading appears twice, CRLF files), vault._sanitize_filename + attachment naming for 80-char titles, exporter.find_note (uuid, exact, substring), client.sign_data against a known-good signature, and client pagination with a fake httpx transport.
4.3 companion.py cmd_serve(): open the browser after uvicorn is actually listening (startup event) rather than before.
4.4 Make /api/sync return HTTP 409 when a sync is already running instead of 200 with code 409.
4.5 Add an option to NOT create a missing daily note (so Obsidian's own daily-note template/Templater runs first), and a "dry run" flag for sync that reports what would change.
4.6 OCR cache: stop rewriting the whole JSON after every page; batch writes per notebook or use sqlite.
4.7 exporter.py export_pdf: register a Unicode TTF font (e.g. DejaVuSans) so emoji/CJK in transcripts do not render as boxes; remove the unused available_h variable.
