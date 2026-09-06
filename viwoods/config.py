import json
from typing import Optional
from pydantic import BaseModel

from .paths import data_path

CONFIG_PATH = data_path("config.json")

NO_TOKEN_MESSAGE = (
    "No Viwoods token configured. Paste your Access-Token from cloud.viwoods.com "
    "into the Settings tab of the web dashboard, or add it to "
    f"{CONFIG_PATH} as \"token\"."
)


class Config(BaseModel):
    # Viwoods Cloud Auth
    token: str = ""
    machine_number: str = ""
    machine_model: str = "web"
    device_name: str = "AiPaper"
    api_base_url: str = "https://api.viwoods.com"
    secret_key: str = "O9EfpIx4g9o8TKuCv2n5msBHucSrAf"

    # Obsidian Vault Settings
    vault_path: str = r"C:\Users\You\Obsidian"
    vault_mirror_folder: str = "Viwoods"
    vault_attachments_folder: str = "Viwoods/Attachments"
    daily_folder: str = "10 - Journals"
    daily_heading: str = "# Transcribed text from AiPaper:"
    # False leaves a missing daily note alone, so Obsidian's own daily-note
    # template (or Templater) creates it first and the sync fills it in later.
    create_missing_daily_notes: bool = True

    # OCR Settings. "windows" only works on Windows, so it cannot be the default.
    ocr_engine: str = "ollama"  # "ollama", "lmstudio", "gemini", "windows"
    gemini_api_key: Optional[str] = ""
    gemini_model: str = "gemini-2.0-flash"
    lmstudio_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = "qwen2.5-vl-7b-instruct"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_think: bool = False

    # Sync Preferences
    auto_sync_interval: int = 0  # 0 = disabled, >0 = minutes (serve mode)
    download_recordings: bool = True  # save meeting audio into the attachments folder
    max_pages_per_notebook: int = 50  # Cap on large imported PDF planners


def load_config() -> Config:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Drop keys from older versions (e.g. the removed mirror_daily)
                # so an existing config file still loads.
                known = set(Config.model_fields)
                return Config(**{k: v for k, v in data.items() if k in known})
        except Exception as e:
            print(f"Warning: Failed to load config from {CONFIG_PATH}: {e}")
    cfg = Config()
    save_config(cfg)
    return cfg


def save_config(cfg: Config) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg.model_dump(), f, indent=2)
