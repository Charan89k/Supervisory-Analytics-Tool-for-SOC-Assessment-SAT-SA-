"""
Ollama backend — DEVELOPMENT convenience, not the shipped product.

Kept because Ollama is the fastest way to try a real model on a
workstation that already has one pulled. It is not the recommended
air-gapped deployment: it needs a background service installed and
running on a TCP port, with its own out-of-band model provisioning —
three separate things for an NCIIPC accreditation to approve. See
LlamaCppBackend for the shipped path, where the model is a data file
rather than installed software.

Talks only to a loopback address. No external network call is made
here or anywhere else in SAT-SA.
"""

from __future__ import annotations

from typing import Optional

from analytics.narration.base import (
    BackendStatus,
    LocalLLMBackend,
    NarrationResult,
    build_prompt,
)


class OllamaBackend(LocalLLMBackend):
    name = "ollama"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.base_url = str(
            self.config.get("base_url", "http://localhost:11434")
        ).rstrip("/")
        self.model = self.config.get("model", "qwen2.5:7b")

    def probe(self) -> BackendStatus:
        """
        Two questions, both answered against the short probe timeout:
        is the service up, and is THIS model actually pulled? A running
        Ollama with a different model is still unusable for us, and
        reporting it as "available" would mean every explanation fails
        one by one at generation time with no visible reason.
        """
        unavailable = lambda detail: BackendStatus(  # noqa: E731
            available=False, backend=self.name, model=self.model, detail=detail)

        try:
            import requests
        except ImportError:
            return unavailable("the 'requests' package is not installed")

        try:
            response = requests.get(
                f"{self.base_url}/api/tags", timeout=self.probe_timeout
            )
            response.raise_for_status()
            installed = [
                m.get("name", "") for m in response.json().get("models", [])
            ]
        except Exception:
            return unavailable(
                f"no Ollama service is responding at {self.base_url}")

        if not any(
            name == self.model or name.startswith(f"{self.model}:")
            for name in installed
        ):
            present = ", ".join(sorted(installed)) or "none"
            return unavailable(
                f"Ollama is running but '{self.model}' is not pulled "
                f"(available locally: {present})")

        return BackendStatus(
            available=True, backend=self.name, model=self.model,
            detail=f"Ollama at {self.base_url}")

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
