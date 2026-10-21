"""
The notes someone would already have in an Obsidian vault, for the demo.

InkBridge doesn't own a vault: it adds a mirrored folder and fills a block in
the user's own daily notes. To show that, the demo vault starts with a daily
note template, two daily notes written from it, and a few project and reading
notes that link to notebooks the sync is about to mirror. All invented.
"""

import json
from pathlib import Path

DAILY_FOLDER = "10 - Journals/11 - Daily"
TEMPLATE = "90 - Templates/Daily note"

CORE_PLUGINS = [
    "file-explorer", "global-search", "switcher", "graph", "backlink", "outgoing-link",
    "tag-pane", "page-preview", "daily-notes", "templates", "note-composer",
    "command-palette", "outline", "word-count",
]

DAILY_TEMPLATE = """---
tags:
  - daily-journal
date: {{date:YYYY-MM-DD}}
---

## 🌅 Landing

## 🗓️ Timeline

# Transcribed text from AiPaper:

## ✅ Check-ins
- [ ] Water
- [ ] Move
- [ ] Shut down by 18:00
"""

DAILY_NOTES = {
    "2026-09-02": """---
tags:
  - daily-journal
date: 2026-09-02
---

## 🌅 Landing

Slow start. Coffee on the balcony, then straight into emails.

## 🗓️ Timeline

- 09:00 Emails and the quarterly summary
- 13:00 Lunch with Sam
- 17:30 Bike chain, finally

# Transcribed text from AiPaper:

## ✅ Check-ins
- [x] Water
- [x] Move
- [ ] Shut down by 18:00
""",
    "2026-09-03": """---
tags:
  - daily-journal
date: 2026-09-03
---

## 🌅 Landing

Rain all morning. Library run after lunch.

## 🗓️ Timeline

- 10:00 Deep work: migration notes
- 15:00 Library
- 19:00 Stew

# Transcribed text from AiPaper:

## ✅ Check-ins
- [x] Water
- [ ] Move
- [x] Shut down by 18:00
""",
}

NOTES = {
    "20 - Projects/Garden.md": """---
tags:
  - project
  - garden
status: active
---

# Garden

Spring beds are planned on paper: see [[99 - Viwoods/Paper/Projects/Garden plan|the sketch]].

## Next
- [ ] Order compost and cedar stakes
- [ ] Start tomato seedlings indoors
- [x] Measure the south bed
""",
    "20 - Projects/Home lab.md": """---
tags:
  - project
  - homelab
status: active
---

# Home lab

Wiring diagram and the to-do list live in [[99 - Viwoods/Paper/Projects/Home network|Home network]].

- [ ] Move the printer to a static address
""",
    "30 - Reading/Book club.md": """---
tags:
  - reading
---

# Book club

Next pick comes from my [[99 - Viwoods/Paper/Reference/Reading list|reading list]].

Friday, 19:00. Bring the notes from [[2026-09-03]].
""",
    "40 - Recipes/Sourdough.md": """---
tags:
  - recipe
  - baking
---

# Sourdough

Working notes: [[99 - Viwoods/Paper/Reference/Sourdough notes|handwritten version]].
""",
}


def seed_vault(vault: Path) -> None:
    """Writes the pre-existing notes and Obsidian settings into `vault`."""
    obsidian = vault / ".obsidian"
    obsidian.mkdir(parents=True, exist_ok=True)
    settings = {
        "daily-notes.json": {"folder": DAILY_FOLDER, "format": "YYYY-MM-DD", "template": TEMPLATE},
        "core-plugins.json": CORE_PLUGINS,
        "app.json": {"alwaysUpdateLinks": True, "showLineNumber": False},
        "appearance.json": {"theme": "obsidian", "baseFontSize": 16},
    }
    for name, content in settings.items():
        (obsidian / name).write_text(json.dumps(content, indent=2), encoding="utf-8")

    def write(relative: str, text: str) -> None:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write(f"{TEMPLATE}.md", DAILY_TEMPLATE)
    for date, text in DAILY_NOTES.items():
        write(f"{DAILY_FOLDER}/{date}.md", text)
    for relative, text in NOTES.items():
        write(relative, text)
