"""
Ollama backend — the DEFAULT zero-configuration AI path.

Chosen as the default because it needs nothing from the user: no
executable path, no model directory, no endpoint, no GGUF file. The
runtime is discovered, the model list comes from the service's own API,
and a single setup script run once provisions both. See
`ollama_runtime` for the discovery itself.

It is the default, not the universal answer. Ollama installs a
background service listening on a TCP port, with model provisioning of
its own — three things an accreditation must approve separately. Where a
resident service is not permitted, `LlamaCppBackend` remains fully
supported and loads a plain GGUF data file in-process instead. Both are
offline; neither is deprecated.

Talks only to a loopback address. No external network call is made here
or anywhere else in SAT-SA.
"""

from __future__ import annotations

from typing import Optional

from analytics.narration.base import (
    STATE_AVAILABLE,
    STATE_MODEL_MISSING,
    STATE_NOT_INSTALLED,
    STATE_NOT_RUNNING,
    STATE_UNAVAILABLE,
    BackendStatus,
    LocalLLMBackend,
    NarrationResult,
    build_prompt,
)
from analytics.narration import ollama_runtime

DEFAULT_MODEL = "qwen2.5:7b"


class OllamaBackend(LocalLLMBackend):
    name = "ollama"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.base_url = str(
            self.config.get("base_url", ollama_runtime.DEFAULT_ENDPOINT)
        ).rstrip("/")
        self.model = self.config.get("model", DEFAULT_MODEL)

    def probe(self) -> BackendStatus:
        """
        Answer not just "can I be used?" but "what is stopping me?"

        A single "unavailable" would be useless here: nothing installed,
        a stopped service, and a healthy service missing the model are
        three different problems with three different fixes, and only
        one of them needs the setup script. Every branch resolves
        against the short probe timeout, so a dead service reports
        itself dead in seconds rather than blocking the window.
        """
        def fail(state: str, detail: str) -> BackendStatus:
            return BackendStatus(available=False, backend=self.name,
                                 model=self.model, detail=detail, state=state)

        try:
            import requests  # noqa: F401
        except ImportError:
            return fail(STATE_UNAVAILABLE,
                        "the 'requests' package is not installed")

        status = ollama_runtime.discover(self.base_url, self.probe_timeout)

        if not status.installed:
            return fail(STATE_NOT_INSTALLED, status.detail)
        if not status.service_running:
            return fail(STATE_NOT_RUNNING, status.detail)

        if not status.has_model(self.model):
            present = ", ".join(status.models) or "none"
            return fail(
                STATE_MODEL_MISSING,
                f"the local AI runtime is running but '{self.model}' is not "
                f"installed (present: {present})")

        return BackendStatus(
            available=True, backend=self.name, model=self.model,
            state=STATE_AVAILABLE, detail=f"Ollama at {self.base_url}")

    def explain(self, finding: dict) -> Optional[NarrationResult]:
        try:
            import requests
        except ImportError:
            return None

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": build_prompt(finding),
                    "stream": False,
                    "options": {"temperature": self.temperature},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = (response.json().get("response") or "").strip()
        except Exception:
            # Unreachable, model missing, or timed out. The caller keeps
            # the deterministic rationale; nothing about the assessment
            # changes.
            return None

        if not text:
            return None

        return NarrationResult(
            text=text, backend=self.name, model=self.model, is_mock=False)
