# Changelog

All notable changes to InkBridge are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Deletes, moves and renames follow the tablet.** A full sync keeps the vault a 1:1 mirror: a
  notebook (or folder) deleted on the device is removed from the vault along with its page images,
  recordings and copies in daily notes, and a notebook that moved or was renamed is rewritten at its
  new path with the old file removed. Switch off with `remove_deleted_notebooks`.
- **A safety limit on deletions.** A sync that would delete more than `max_deletions_per_sync`
  notebooks (default 10), or more than half of a small library, deletes nothing and waits for
  `sync --confirm-deletions` or an OK in the dashboard. It guards against a wrong account or a bad
  response from the cloud.
- **`login`**: `inkbridge login` signs in with an email and a hidden password (or `--token` to paste
  one) and stores the token. The password is never saved and the token is never printed.
- **Automatic token renewal.** A token the API rejects is renewed once and the request retried, and
  one close to expiry is renewed ahead of time.
- **Alerts.** Set `notify_url` (an ntfy topic or a Discord/Slack webhook) and an unattended sync
  sends one message when it needs you (sign-in rejected, vault missing, repeated failures, deletions
  held back) and one when it's fixed. `test-notify` checks delivery.
- **`doctor`**: checks Python and packages, the config, the Viwoods sign-in, the vault and the OCR
  engine, says how to fix each problem, and prints nothing secret, so it's safe to paste into a bug
  report. `status` also shows how the last sync went.
- **Install with pip or pipx.** `pyproject.toml` provides an `inkbridge` command and ships the
  dashboard files.
- Community files: `CONTRIBUTING.md`, `SECURITY.md`, issue forms, a pull request template, and
  Dependabot.
- **A demo you can run without a tablet.** `python contrib/demo/run_demo.py` starts the dashboard
  against a pretend Viwoods Cloud and a throwaway vault seeded with ordinary notes, and the README
  now shows screenshots made from it (the daily note, a mirrored notebook, the dashboard).
- The dashboard's header shows the installed version instead of a fixed `v1.0`.

### Changed

- Messages that tell you what to run say `inkbridge ...` for an installed copy and
  `python companion.py ...` from a checkout.
- A pip/pipx install exports to `~/.viwoods/exports` instead of next to the code.
- The daemon re-reads the token every cycle, so `login` after an expiry is picked up without a
  restart, and it checks again within 30 minutes after a rejected sign-in.
- `sync --dry-run` also reports notebooks that would be moved, deleted, held back, or have a stale
  copy removed.
- A missing Python package at startup now explains itself and points at the project's virtual
  environment.

### Fixed

- The README (and a code comment) said the `%% … %%` markers InkBridge writes are hidden in Live
  Preview. In Obsidian they are invisible in Reading view but shown dimmed in the editor; the docs now
  say so.
- Daily app entries whose date the API sends without zero-padding (`2026-10-1`) were never found in
  the vault or recorded, so they were reprocessed on every sync.
- The API's `401 Incorrect signature` reply was reported as an expired token.
- Copies of a notebook left behind by earlier moves and renames are removed.
- A page deleted from a notebook no longer leaves its image in the attachments folder.
- When the sync state file didn't exist yet, every caller shared the same default `notes` dictionary.

### Security

- `config.json` (token, API keys, alert address) is now readable only by your user (`0600`), and
  the default data directory and the cache of page scans are `0700`. Files from older versions are
  tightened when they're next loaded.

## Before this changelog

InkBridge was developed in the open from 2026-09-05 without tagged releases. By the end of
2026-10-15 it could already:

- mirror a tablet's folder tree into an Obsidian vault and inject transcripts into daily notes
  between hidden markers, using the vault's own Daily notes settings;
- sync the Daily app, transcribe handwriting with Ollama, LM Studio, Gemini or Windows OCR (cached
  per engine and model), and optionally infer tags and tasks;
- run as a dashboard or a daemon, in Docker or as a systemd service, waiting for a quiet GPU before
  local model calls (`gpu_busy_limit`);
- export notes to PDF, HTML or ZIP;
- keep the dashboard local-only, refusing requests from other websites.

The full history is in the [commit log](https://github.com/desyncc/inkbridge/commits/main).

[Unreleased]: https://github.com/desyncc/inkbridge/commits/main
