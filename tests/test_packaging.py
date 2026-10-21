"""
Installing with pip/pipx: the metadata agrees with the code, the command
hints match how InkBridge was installed, and an installed copy never writes
into site-packages.
"""

import re
import sys
from pathlib import Path

import pytest

import companion
import viwoods
from viwoods import exporter, paths

ROOT = Path(__file__).resolve().parent.parent


def pyproject():
    tomllib = pytest.importorskip("tomllib", reason="Python 3.11+ reads TOML")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_version_comes_from_the_package():
    meta = pyproject()
    assert "version" in meta["project"]["dynamic"]
    assert meta["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "viwoods.__version__"}
    assert re.fullmatch(r"\d+\.\d+\.\d+", viwoods.__version__)


def test_dependencies_come_from_requirements_txt_so_there_is_one_list():
    meta = pyproject()
    assert "dependencies" in meta["project"]["dynamic"]
    assert meta["tool"]["setuptools"]["dynamic"]["dependencies"] == {"file": ["requirements.txt"]}


def test_the_readme_promises_the_python_the_metadata_requires():
    meta = pyproject()
    assert meta["project"]["requires-python"] == ">=3.10"
    assert "3.10" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_inkbridge_command_runs_the_same_main_as_companion_py():
    meta = pyproject()
    assert meta["project"]["scripts"] == {"inkbridge": "companion:main"}
    assert callable(companion.main)


def test_the_dashboard_files_ship_with_the_package():
    meta = pyproject()
    assert meta["tool"]["setuptools"]["packages"] == ["viwoods"]
    assert meta["tool"]["setuptools"]["py-modules"] == ["companion"]
    shipped = meta["tool"]["setuptools"]["package-data"]["viwoods"]
    assert "web/*" in shipped
    assert (ROOT / "viwoods" / "web" / "index.html").is_file()


# --- how to tell the user to run a command -------------------------------------

def test_hints_name_companion_py_from_a_checkout():
    assert paths.command("login", installed=False) == "python companion.py login"


def test_hints_name_the_installed_command_when_installed():
    assert paths.command("sync --dry-run", installed=True) == "inkbridge sync --dry-run"


@pytest.mark.parametrize("parent, expected", [
    ("/home/me/.local/pipx/venvs/inkbridge/lib/python3.12/site-packages", True),
    ("/usr/lib/python3/dist-packages", True),
    ("/home/me/Projects/inkbridge", False),
    ("/app", False),
])
def test_installed_means_living_in_site_packages(parent, expected):
    assert paths.is_installed(Path(parent)) is expected


def test_the_checkout_in_front_of_us_is_not_an_install():
    assert paths.is_installed() is False


# --- where exports go -----------------------------------------------------------

def test_exports_go_beside_a_checkout_and_in_the_data_directory_when_installed(tmp_path):
    assert exporter.default_export_dir(Path("/home/me/inkbridge")) == Path("/home/me/inkbridge/exports")
    assert exporter.default_export_dir(Path("/app")) == Path("/app/exports")   # Docker keeps its documented path
    installed = exporter.default_export_dir(Path("/x/venv/lib/python3.12/site-packages"))
    assert installed == paths.data_path("exports")


# --- the friendly error for a missing dependency -----------------------------------

def test_a_missing_dependency_points_at_the_virtualenv_next_to_the_script(tmp_path):
    exe = "bin/python" if sys.platform != "win32" else "Scripts/python.exe"
    (tmp_path / ".venv" / Path(exe).parent).mkdir(parents=True)
    (tmp_path / ".venv" / exe).write_text("")

    message = companion.missing_dependency_message(ModuleNotFoundError(name="httpx"), "/usr/bin/python", tmp_path)

    assert "httpx" in message
    assert "/usr/bin/python" in message
    assert str(tmp_path / ".venv" / exe) in message


def test_a_missing_dependency_without_a_virtualenv_says_to_install_requirements(tmp_path):
    message = companion.missing_dependency_message(ModuleNotFoundError(name="httpx"), "/usr/bin/python", tmp_path)

    assert "pip install -r requirements.txt" in message
    assert ".venv" not in message.replace("virtual environment", "")
