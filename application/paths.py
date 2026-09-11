"""
Where SAT-SA reads from and where it writes to.

Running from source, both answers are "the project directory". Frozen
into a single executable they are different places, and getting them
confused is silently destructive:

  * Read-only resources (the rule configuration) live inside the
    bundle. PyInstaller unpacks them to a temporary directory whose
    location changes on every launch.
  * Assessment history must OUTLIVE the process. Writing it beside the
    executable fails under Program Files, and writing it into the
    unpack directory succeeds and then disappears when the application
    exits — the worse of the two failures, because nothing reports it.

Both answers are computed here so no module has to guess.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: The product's folder name wherever it stores per-user data.
APP_DIR_NAME = "SAT-SA"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """
    The directory holding bundled read-only resources (config/, docs/).

    Frozen, this is PyInstaller's unpack directory. From source it is
    the project root. Never write here.
    """
    if is_frozen():
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            return Path(bundled)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_path() -> Path:
    """The assessment rule configuration in use."""
    return resource_root() / "config" / "assessment_rules.yaml"


def user_data_root() -> Path:
    """
    A per-user, writable directory that survives restarts and upgrades.

    From source this stays inside the project so a developer's runs
    remain where they have always been. Frozen, it moves under the
    user's own profile, which is writable without elevation and is not
    erased when the bundle is unpacked again.
    """
    if not is_frozen():
        return Path(__file__).resolve().parents[1]

    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents"
    elif sys.platform == "darwin":
        base = Path.home() / "Documents"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME")
                    or (Path.home() / ".local" / "share"))
    return base / APP_DIR_NAME


def default_history_root() -> Path:
    """Where assessment runs are stored unless the operator moves them."""
    return user_data_root() / "assessments"
