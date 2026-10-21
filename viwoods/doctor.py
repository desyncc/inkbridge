"""
`doctor`: one report on whether this install can sync, and what to do about
whatever can't.

Each check answers a single question (does the Viwoods sign-in work? is the
vault there and writable? is the OCR engine running with its model?) and says
how to fix it. The report never contains your token, API keys, alert address
or home directory, so it is safe to paste into a bug report.
"""

import importlib.metadata
import os
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

from . import __version__, config as config_module, health
from .client import AuthError, ViwoodsClient, token_expiry
from .config import Config, ConfigError, load_config
from .paths import command
from .vault import ObsidianVault

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
HOME = Path.home()
MIN_PYTHON = (3, 10)
PACKAGES = ("httpx", "fastapi", "uvicorn", "pydantic", "pillow", "reportlab")
LOCAL_SERVER_TIMEOUT = 5.0


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


# --- the checks ---------------------------------------------------------------

def check_python() -> List[Check]:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info[:2] < MIN_PYTHON:
        return [Check("Python", FAIL, f"{version} is too old",
                      f"Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")]
    return [Check("Python", OK, f"{version} on {platform.system()} {platform.release()}")]


def check_packages() -> List[Check]:
    found, missing = [], []
    for name in PACKAGES:
        try:
            found.append(f"{name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return [Check("Packages", FAIL, f"missing: {', '.join(missing)}",
                      "python -m pip install -r requirements.txt")]
    return [Check("Packages", INFO, ", ".join(found))]


def load_settings() -> Tuple[List[Check], Optional[Config]]:
    """Reads config.json without creating it (a diagnosis shouldn't change anything)."""
    path = config_module.CONFIG_PATH
    if not path.exists():
        return [Check("Config file", INFO, f"{path} doesn't exist yet; defaults are in use")], Config()
    try:
        return [Check("Config file", OK, str(path))], load_config()
    except ConfigError as e:
        return [Check("Config file", FAIL, str(e), f"Fix {path}, or delete it to start over")], None


def check_account(cfg: Config) -> List[Check]:
    if not (cfg.token or "").strip():
        return [Check("Viwoods account", FAIL, "no sign-in token is stored", command("login"))]

    checks = []
    expires = token_expiry(cfg.token)
    if expires is not None:
        days = (expires - time.time()) / 86400
        if days >= 0:
            note = " (InkBridge renews it by itself)" if days < 30 else ""
            checks.append(Check("Sign-in token", INFO, f"expires in {int(days)} days{note}"))
        else:
            checks.append(Check("Sign-in token", INFO, f"expired {int(-days)} days ago"))

    try:
        devices = ViwoodsClient(cfg).get_devices()
    except AuthError:
        checks.append(Check("Viwoods account", FAIL, "Viwoods rejected the stored sign-in", command("login")))
    except (httpx.TransportError, OSError) as e:
        checks.append(Check("Viwoods account", FAIL,
                            f"could not reach Viwoods Cloud ({type(e).__name__})",
                            f"Check your internet connection, and that api_base_url ({cfg.api_base_url}) is right"))
    except Exception as e:
        checks.append(Check("Viwoods account", FAIL, f"unexpected reply: {e}"))
    else:
        if devices:
            d = devices[0]
            checks.append(Check("Viwoods account", OK, f"signed in; tablet: {d.get('modelName')}"))
        else:
            checks.append(Check("Viwoods account", WARN, "signed in, but no tablet is registered to this account",
                                "Sign in to the Viwoods app on the tablet with the same account"))
    return checks


def check_vault(cfg: Config) -> List[Check]:
    if cfg.vault_path == Config.model_fields["vault_path"].default:
        return [Check("Obsidian vault", FAIL, "vault_path is still the placeholder",
                      "Set vault_path to your vault's folder (Settings tab, or config.json)")]
    path = Path(os.path.expanduser(cfg.vault_path))
    if not path.is_dir():
        return [Check("Obsidian vault", FAIL, f"no folder at {path}",
                      "Fix vault_path (Settings tab, or config.json)")]
    if not os.access(path, os.W_OK | os.X_OK):
        return [Check("Obsidian vault", FAIL, f"{path} isn't writable by this user",
                      "Fix the folder's permissions, or run InkBridge as the user who owns the vault")]

    checks = []
    if not (path / ".obsidian").is_dir():
        checks.append(Check("Obsidian vault", WARN,
                            f"{path} has no .obsidian folder, so it doesn't look like a vault",
                            "Open this folder once in Obsidian, or ignore this if it's intentional"))
    else:
        checks.append(Check("Obsidian vault", OK, str(path)))
    try:
        daily = ObsidianVault(cfg).describe_daily_notes()
        where = "Obsidian's Daily notes settings" if daily["source"] == "obsidian" else "daily_folder in config"
        checks.append(Check("Daily notes", INFO, f"{daily['folder'] or '(vault root)'} ({where})"))
    except Exception:
        pass
    return checks


def _installed(wanted: str, available: List[str]) -> bool:
    return wanted in available or (":" not in wanted and f"{wanted}:latest" in available)


def _local_models(url: str, key: str) -> List[str]:
    reply = httpx.get(url, timeout=LOCAL_SERVER_TIMEOUT)
    reply.raise_for_status()
    body = reply.json()
    return [m.get("name") or m.get("id") or "" for m in body.get(key, [])]


def check_ocr(cfg: Config) -> List[Check]:
    engine = cfg.ocr_engine
    if engine == "ollama":
        base = cfg.ollama_url.rstrip("/")
        try:
            models = _local_models(f"{base}/api/tags", "models")
        except Exception:
            return [Check("Ollama", FAIL, f"not reachable at {cfg.ollama_url}",
                          "Start it with `ollama serve` (install from https://ollama.com), "
                          "or point ollama_url at the machine running it")]
        if not _installed(cfg.ollama_model, models):
            return [Check("Ollama", FAIL, f"running, but the model {cfg.ollama_model} isn't downloaded",
                          f"ollama pull {cfg.ollama_model}")]
        return [Check("Ollama", OK, f"running; model {cfg.ollama_model} is available")]

    if engine == "lmstudio":
        base = cfg.lmstudio_url.rstrip("/")
        try:
            models = _local_models(f"{base}/models", "data")
        except Exception:
            return [Check("LM Studio", FAIL, f"not reachable at {cfg.lmstudio_url}",
                          "Start LM Studio's local server and load a vision model")]
        if cfg.lmstudio_model not in models:
            return [Check("LM Studio", FAIL, f"running, but {cfg.lmstudio_model} isn't loaded",
                          "Load that model in LM Studio, or change lmstudio_model")]
        return [Check("LM Studio", OK, f"running; model {cfg.lmstudio_model} is loaded")]

    if engine == "gemini":
        if not (cfg.gemini_api_key or "").strip():
            return [Check("Gemini", FAIL, "no API key is stored",
                          "Add a Google AI Studio key in the Settings tab (aistudio.google.com)")]
        return [Check("Gemini", OK, f"API key set (not tested); model {cfg.gemini_model}. "
                                    "Page scans are sent to Google for transcription")]

    if engine == "windows":
        if os.name != "nt":
            return [Check("Windows OCR", FAIL, "only works on Windows",
                          f"Switch engine: {command('set-engine ollama')}")]
        return [Check("Windows OCR", OK, "available")]

    return [Check("OCR engine", FAIL, f"unknown engine {engine!r}",
                  "Use ollama, lmstudio, gemini or windows")]


def check_alerts(cfg: Config) -> List[Check]:
    if (cfg.notify_url or "").strip():
        return [Check("Alerts", INFO, "on (notify_url is set)", f"Test it: {command('test-notify')}")]
    return [Check("Alerts", INFO, "off",
                  "Set notify_url to hear about a stalled sync (see the README's Alerts section)")]


def check_last_sync() -> List[Check]:
    state = health.load_health()
    status = state.get("status")
    if status in ("failing", "attention"):
        return [Check("Last sync", WARN, health.describe_health(state))]
    return [Check("Last sync", INFO, health.describe_health(state))]


# --- running and showing them ----------------------------------------------------------

def run_checks() -> List[Check]:
    """Runs every check; one that crashes becomes a failure row instead of ending the report."""
    checks: List[Check] = []

    def run(check, *args) -> None:
        try:
            checks.extend(check(*args))
        except Exception as e:
            checks.append(Check(getattr(check, "__name__", "check"), FAIL, f"the check itself crashed: {e}"))

    run(check_python)
    run(check_packages)
    config_checks, cfg = load_settings()
    checks.extend(config_checks)
    if cfg is not None:
        run(check_account, cfg)
        run(check_vault, cfg)
        run(check_ocr, cfg)
        run(check_alerts, cfg)
    run(check_last_sync)

    secrets = [s for s in (getattr(cfg, "token", ""), getattr(cfg, "gemini_api_key", ""),
                           getattr(cfg, "notify_url", "")) if s and len(s) > 6]
    for c in checks:
        for secret in secrets:
            c.detail, c.fix = c.detail.replace(secret, "***"), c.fix.replace(secret, "***")
    return checks


def has_failures(checks: List[Check]) -> bool:
    return any(c.status == FAIL for c in checks)


def _tidy(text: str) -> str:
    """Shows the home directory as ~, so a pasted report doesn't carry the username."""
    for home in {str(HOME), str(HOME.resolve())}:
        text = text.replace(home, "~")
    return text


def render(checks: List[Check]) -> str:
    marker = {OK: "  ok", WARN: "WARN", FAIL: "FAIL", INFO: "info"}
    lines = [
        f"InkBridge doctor - v{__version__}, Python {platform.python_version()}, "
        f"{platform.system()} {platform.release()}",
        "Nothing below contains your token, keys or alert address, so it is safe to paste into an issue.",
        "",
    ]
    for c in checks:
        lines.append(f"[{marker[c.status]}] {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"       -> {c.fix}")
    problems = sum(c.status == FAIL for c in checks)
    warnings = sum(c.status == WARN for c in checks)
    lines.append("")
    if problems:
        lines.append(f"{problems} problem{'s' if problems != 1 else ''} to fix"
                     + (f", {warnings} warning{'s' if warnings != 1 else ''}." if warnings else "."))
    else:
        lines.append("No problems found." + (f" {warnings} warning{'s' if warnings != 1 else ''}." if warnings else ""))
    return _tidy("\n".join(lines))
