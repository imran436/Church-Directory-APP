"""
config.py — Configuration loader.

Merges config.json (developer defaults) with config.local.json (church overrides).
config.local.json always wins. Neither file is required to contain all keys.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from models import AppConfig
from errors import ConfigError


# ── Locate config files relative to the executable or script ─────────────────

def _app_dir() -> Path:
    """
    Return the directory containing the running executable or script.
    Works both when run as a script (development) and as a PyInstaller bundle.
    """
    if getattr(__import__('sys'), 'frozen', False):
        # Running as PyInstaller bundle — executable is sys.executable
        return Path(__import__('sys').executable).parent
    # Running as a plain Python script
    return Path(__file__).parent


APP_DIR       = _app_dir()
CONFIG_PATH   = APP_DIR / "config.json"
LOCAL_PATH    = APP_DIR / "config.local.json"


# ── Default configuration ─────────────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    "church_name":            "The Gathering Church",
    "church_tagline":         "Celebrating Jesus Together",
    "church_address":         "5921 SE 88th Ave, Portland, OR 97266",
    "church_phone":           "(503) 771-7379",
    "church_email":           "connect@gatheringcc.org",
    "church_service":         "Sundays at 10:00 am",
    "directory_year":         None,          # None = use current year at runtime
    "list_id":                "",            # Must be set in config.local.json
    "membership_type_label":  "Member",
    "use_goes_by_name":       True,
    "entries_per_page":       4,
    "photo_pool_size":        8,
    "fuzzy_match_threshold":  92,
    "pdf_engine":             "weasyprint",
    "max_run_logs":           10,
    "keychain_service":       "ChurchDirectoryGenerator",
    "output_filename_format": "GatheringDirectory_{date}.pdf",
}


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file. Return empty dict if file doesn't exist."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Cannot parse {path.name}: {e}") from e


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base. Override values win. Returns new dict."""
    result = dict(base)
    for key, value in override.items():
        result[key] = value
    return result


def load_config() -> AppConfig:
    """
    Load and merge configuration. Returns a fully-populated AppConfig.
    Raises ConfigError if required fields are missing.
    """
    base    = _load_json(CONFIG_PATH)
    local   = _load_json(LOCAL_PATH)

    # Start from hardcoded defaults, merge in file values
    merged = _deep_merge(DEFAULTS, base)
    merged = _deep_merge(merged, local)

    # Resolve directory_year default
    if not merged.get("directory_year"):
        merged["directory_year"] = str(datetime.now().year)

    return AppConfig(
        church_name           = merged["church_name"],
        church_tagline        = merged["church_tagline"],
        church_address        = merged["church_address"],
        church_phone          = merged["church_phone"],
        church_email          = merged["church_email"],
        church_service        = merged["church_service"],
        directory_year        = str(merged["directory_year"]),
        list_id               = str(merged.get("list_id", "")),
        membership_type_label = merged["membership_type_label"],
        use_goes_by_name      = bool(merged["use_goes_by_name"]),
        entries_per_page      = int(merged["entries_per_page"]),
        photo_pool_size       = int(merged["photo_pool_size"]),
        fuzzy_match_threshold = int(merged["fuzzy_match_threshold"]),
        pdf_engine            = merged["pdf_engine"],
        max_run_logs          = int(merged["max_run_logs"]),
        keychain_service      = merged["keychain_service"],
        output_filename_format= merged["output_filename_format"],
    )


def save_local(updates: dict[str, Any]) -> None:
    """
    Write key-value pairs to config.local.json.
    Merges with existing local config — never overwrites unrelated keys.
    """
    existing = _load_json(LOCAL_PATH)
    merged   = _deep_merge(existing, updates)
    LOCAL_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def has_list_id() -> bool:
    """Return True if a list ID has been configured."""
    local = _load_json(LOCAL_PATH)
    base  = _load_json(CONFIG_PATH)
    merged = _deep_merge(DEFAULTS, base)
    merged = _deep_merge(merged, local)
    return bool(merged.get("list_id", "").strip())
