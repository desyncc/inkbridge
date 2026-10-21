"""
The README keeps up with the code: every setting and command is documented,
and every link to a file in the repo goes somewhere.
"""

import re
from pathlib import Path

from viwoods.config import Config

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

# Filled in by InkBridge itself or fixed by the Viwoods API, never set by hand.
INTERNAL_SETTINGS = {"machine_number", "machine_model", "device_name", "secret_key"}


def test_every_setting_is_documented():
    missing = [
        name for name in Config.model_fields
        if name not in INTERNAL_SETTINGS and f"`{name}`" not in README
    ]
    assert not missing, f"add these to the README's settings table: {missing}"


def test_every_command_is_documented():
    commands = re.findall(r'add_parser\("([a-z-]+)"', (ROOT / "companion.py").read_text(encoding="utf-8"))
    assert commands
    missing = [c for c in commands if c not in ("gui",) and f"companion.py {c}" not in README]
    assert not missing, f"document these commands in the README: {missing}"


def test_links_to_files_in_the_repo_resolve():
    broken = []
    for doc in ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md"]:
        path = ROOT / doc
        if not path.exists():
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (ROOT / target).exists():
                broken.append(f"{doc} -> {target}")
    assert not broken, broken


def test_the_docs_a_visitor_expects_exist():
    for name in ["README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md"]:
        assert (ROOT / name).is_file(), f"missing {name}"
    assert (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").is_file()
    assert list((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))


def test_the_changelog_has_an_unreleased_section_or_the_current_version():
    import viwoods

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text or f"## [{viwoods.__version__}]" in text
