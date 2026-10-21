# Contributing

Thanks for wanting to help. Bug reports, fixes and small features are all welcome.

## Reporting a bug

Run `inkbridge doctor` (or `python companion.py doctor` from a checkout) and paste its output into
the [bug report form](https://github.com/desyncc/inkbridge/issues/new/choose). It contains no token, key or alert address, and it
answers most of the questions I'd otherwise ask. For anything security-related, see
[SECURITY.md](SECURITY.md) instead.

## Setting up

You need Python 3.10 or newer.

```bash
git clone https://github.com/desyncc/inkbridge.git
cd inkbridge
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

The tests run against a temporary data directory and a fake Viwoods API, so they never touch your
real config, vault or account. Please keep it that way: **no real tokens, passwords or notebook
content in tests or fixtures.**

## Trying it without a tablet

`python contrib/demo/run_demo.py` starts the real dashboard against a pretend Viwoods Cloud
(`contrib/demo/fake_viwoods.py`) and a throwaway vault seeded with notes someone might already have,
so you can work on the dashboard or the vault code without an account or any real notebooks. The
README's screenshots come from the same demo: `contrib/demo/screenshots.py` and
`contrib/demo/obsidian_screenshots.py` regenerate them (they need `pip install playwright`, and
Obsidian installed for the second one).

## How the code is laid out

| Path | What it does |
| --- | --- |
| `companion.py` | The command line (`inkbridge ...`), the daemon loop |
| `viwoods/client.py` | The Viwoods Cloud API: request signing, paging, sign-in, token renewal |
| `viwoods/sync.py` | The sync engine: what to fetch, what changed, what to remove |
| `viwoods/vault.py` | Everything written into the Obsidian vault |
| `viwoods/ocr.py` | The handwriting engines (Ollama, LM Studio, Gemini, Windows) and their cache |
| `viwoods/health.py`, `viwoods/doctor.py` | Alerts, the last-sync record, the `doctor` report |
| `viwoods/server.py`, `viwoods/web/` | The local dashboard |
| `contrib/demo/` | The demo: a fake Viwoods Cloud, a seeded vault, and the screenshot scripts |
| `tests/` | One file per area |

## Making a change

- **Write the test first.** A bug fix starts with a test that fails for the right reason; a feature
  starts with tests describing what it should do. Reviewing is far quicker that way.
- **Keep it focused.** One change per pull request, matching the surrounding code's style and
  comment density. Comments should say *why*, not repeat what the code does.
- **Update the docs in the same change.** A new setting or command goes in the README (a test
  fails if you forget), and anything a user would notice goes under *Unreleased* in
  [CHANGELOG.md](CHANGELOG.md).
- **Be careful with the vault.** InkBridge writes into people's notes and, since it mirrors
  deletions, removes files. Anything that deletes or overwrites needs a test showing what it does
  and doesn't touch.
- **Lint:** `pip install ruff && ruff check .` should be clean (CI runs it).

Commit messages: a short imperative summary line (`Fix ...`, `Add ...`), then a paragraph on *why*
if it isn't obvious.

## Pull requests

Open one against `main`. CI runs the tests on Linux and Windows and checks the package installs.
Describe what changed and how you tested it; the template will prompt you.
