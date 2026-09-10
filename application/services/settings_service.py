"""
Local settings persistence.

Settings live in a plain JSON file under the user's config directory.
Deliberately not a binary or registry-backed store: an air-gapped
deployment is auditable, and an operator asked "what model was this
assessment configured to use?" should be able to answer with `cat`.

Nothing here is synchronised anywhere. There is no network path.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from application.services.ai_config import AIConfig

APP_NAME = "SAT-SA"
SETTINGS_FILENAME = "settings.json"


def settings_directory() -> Path:
    """
    Per-user config directory, resolved without Qt so this module stays
    importable headless (tests, CLI) where no QApplication exists.
    """
    override = os.environ.get("SATSA_CONFIG_DIR")
    if override:
        return Path(override)

    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")

    return Path(base) / APP_NAME


def settings_path() -> Path:
    return settings_directory() / SETTINGS_FILENAME


class SettingsService:
    """Loads and saves the desktop application's local settings."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else settings_path()

    # -- AI ----------------------------------------------------------

    def load_ai_config(self, default: Optional[AIConfig] = None) -> AIConfig:
        """
        Read the stored AI configuration.

        A corrupt or partial file returns the default rather than
        raising: a settings file is not worth failing to start over,
        and the supervisor can simply reconfigure.
        """
        fallback = default or AIConfig()

        try:
            with open(self.path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            return fallback

        section = stored.get("ai")
        if not isinstance(section, dict):
            return fallback

        known = {f for f in asdict(fallback)}
        merged = {**asdict(fallback),
                  **{k: v for k, v in section.items() if k in known}}

        try:
            return AIConfig(**merged)
        except (TypeError, ValueError):
            return fallback

    def save_ai_config(self, config: AIConfig) -> Path:
        stored = {}
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as handle:
                    stored = json.load(handle)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                stored = {}
        if not isinstance(stored, dict):
            stored = {}

        stored["ai"] = asdict(config)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temporary file so an interrupted save cannot leave
        # a truncated settings file behind.
        temp = self.path.with_suffix(".json.tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(stored, handle, indent=2, sort_keys=True)
        os.replace(temp, self.path)

        return self.path
