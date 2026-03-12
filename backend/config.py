"""Configuration loading and saving.

Sensitive credentials (Supabase, Spotify) are read from environment variables
loaded via python-dotenv.  Non-secret preferences (paths, playlists, polling)
live in config.json.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "monitored_playlists": [],
    "auto_scan_playlists": [],
    "downloads_folder": "~/Downloads",
    "external_drive_path": "/Volumes/My Passport/Music/iTunes/iTunes Media/Music/Unknown Artist/Unknown Album",
    "playlist_mapping": {
        "Disco+Melodic": "Disco & Melodic",
        "House": "House - Hot Since 82",
        "Worldtech": "Worldtech Latin Afro",
        "Tech House": "Tech House",
    },
    "poll_interval_minutes": 30,
    "file_watch_enabled": True,
}

_config: dict[str, Any] = {}


def load_config() -> dict[str, Any]:
    """Load config from disk, creating default if missing."""
    global _config
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            _config = json.load(f)
        logger.info("Config loaded from %s", CONFIG_PATH)
    else:
        _config = _DEFAULT_CONFIG.copy()
        save_config(_config)
        logger.info("Created default config at %s", CONFIG_PATH)
    return _config


def save_config(data: dict[str, Any]) -> None:
    """Persist config to disk."""
    global _config
    _config = data
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Config saved to %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    """Return the current in-memory config (call load_config first)."""
    if not _config:
        return load_config()
    return _config


# ---------------------------------------------------------------------------
# Environment-based credential helpers
# ---------------------------------------------------------------------------

def get_supabase_creds() -> dict[str, str]:
    """Return Supabase URL and anon key from environment variables."""
    return {
        "url": os.environ.get("SUPABASE_URL", ""),
        "anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
    }


def get_spotify_creds() -> dict[str, str]:
    """Return Spotify client credentials from environment variables."""
    return {
        "client_id": os.environ.get("SPOTIFY_CLIENT_ID", ""),
        "client_secret": os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    }
