"""
Automatic discovery of a local Ollama runtime.

The user must never be asked for an executable path, a model directory,
or an endpoint. Everything here is discovered: the executable through
platform-appropriate locations, the service through its own loopback
API, and the model list through that API rather than through Ollama's
internal storage.

**No filesystem coupling to Ollama's model store.** SAT-SA asks the
service what it has; it never reads Ollama's blobs or manifests. Ollama
owns model storage and lifecycle, and its layout is free to change.

Nothing here installs, starts, downloads or modifies anything. Discovery
is read-only — installation is the setup script's job, run deliberately
by the user. A tool that silently installed a service would be a poor
citizen on a supervised machine.

Every function is pure enough to stub, so the whole matrix — not
installed, installed but stopped, running without the model, ready —
is testable without Ollama present.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

#: Ollama's own default. Loopback only — never a LAN address.
DEFAULT_ENDPOINT = "http://localhost:11434"

#: Where Ollama installs itself, per platform. Checked after PATH.
INSTALL_LOCATIONS = {
    "Windows": [
        r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
        r"%ProgramFiles%\Ollama\ollama.exe",
        r"%ProgramFiles(x86)%\Ollama\ollama.exe",
    ],
    "Darwin": [
        "/usr/local/bin/ollama",
        "/opt/homebrew/bin/ollama",
        "/Applications/Ollama.app/Contents/Resources/ollama",
    ],
    "Linux": [
        "/usr/local/bin/ollama",
        "/usr/bin/ollama",
        "~/.local/bin/ollama",
        "/opt/ollama/bin/ollama",
    ],
}


@dataclass
class RuntimeStatus:
    """What was discovered about the local Ollama runtime."""

    installed: bool = False
    executable: str = ""
    service_running: bool = False
    endpoint: str = DEFAULT_ENDPOINT
    models: List[str] = field(default_factory=list)
    #: Why discovery could not go further, for the user.
    detail: str = ""

    def has_model(self, name: str) -> bool:
        """
        Whether a model is present.

        Ollama reports tagged names, so a request for `qwen2.5:7b`
        matches `qwen2.5:7b` exactly, and a request for `qwen2.5`
        matches any tag of it.
        """
        wanted = str(name or "").strip()
        if not wanted:
            return False
        return any(
            available == wanted or available.startswith(f"{wanted}:")
            for available in self.models
        )


def find_executable() -> Optional[str]:
    """
    Locate the Ollama executable, or None.

    PATH first, since that is where a supported install puts it, then
    the platform's known install locations for the case where it is
    installed but the shell environment has not picked it up — common on
    Windows immediately after installation, before a new session starts.
    """
    found = shutil.which("ollama")
    if found:
        return found

    for candidate in INSTALL_LOCATIONS.get(platform.system(), []):
        expanded = os.path.expanduser(os.path.expandvars(candidate))
        # expandvars leaves unresolved %VARS% intact; skip those.
        if "%" in expanded:
            continue
        if os.path.isfile(expanded):
            return expanded

    return None


def query_service(endpoint: str = DEFAULT_ENDPOINT, timeout: int = 3):
    """
    Ask the local service what models it has.

    Returns (running, models). Never raises: a dead service is an
    ordinary, expected answer, not an error.
    """
    try:
        import requests
    except ImportError:
        return False, []

    try:
        response = requests.get(f"{endpoint.rstrip('/')}/api/tags",
                                 timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return False, []

    models = [
        str(entry.get("name", ""))
        for entry in (payload.get("models") or [])
        if entry.get("name")
    ]
    return True, sorted(models)


def discover(endpoint: str = DEFAULT_ENDPOINT,
              timeout: int = 3) -> RuntimeStatus:
    """
    Everything SAT-SA needs to know about the local runtime, in one
    read-only pass.

    The service is queried even when no executable was found: Ollama may
    be running in a container or under a service manager that puts
    nothing on this user's PATH, and a responding service is proof
    enough. A responding service therefore counts as installed.
    """
    executable = find_executable()
    running, models = query_service(endpoint, timeout)

    status = RuntimeStatus(
        installed=bool(executable) or running,
        executable=executable or "",
        service_running=running,
        endpoint=endpoint,
        models=models,
    )

    if not status.installed:
        status.detail = "Ollama was not detected on this machine."
    elif not running:
        where = f" ({executable})" if executable else ""
        status.detail = (
            f"Ollama is installed{where} but its local service is not "
            f"responding at {endpoint}.")
    elif not models:
        status.detail = (
            f"The Ollama service is running at {endpoint} but no models "
            f"are installed.")

    return status
