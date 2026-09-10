"""
llama.cpp backend — the SHIPPED inference path.

Chosen over Ollama for air-gapped NCIIPC deployment because the model
becomes a data file rather than installed software: an operator copies
a single .gguf onto the machine on the same media as the dataset, and
points SAT-SA at it. There is no service to install, no daemon to keep
running, no port to open, and no separate model-provisioning mechanism
for an accreditation process to review.

`llama_cpp` is an optional import on purpose. A SAT-SA install with no
inference library present must still start, still run assessments, and
still report AI status honestly — so the import lives inside the calls
and its absence is a status message, not an ImportError at startup.

Model update mechanism: replace the .gguf file and restart. The path,
the file's size and its modification time are reported through
`probe()` so an assessment can record exactly which model artefact was
in place when it ran.
"""

from __future__ import annotations

import os
from typing import Optional

from analytics.narration.base import (
    STATE_NOT_CONFIGURED,
    STATE_UNAVAILABLE,
    BackendStatus,
    LocalLLMBackend,
    NarrationResult,
    build_prompt,
)


class LlamaCppBackend(LocalLLMBackend):
    name = "llamacpp"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.model_path = os.path.expanduser(
            str(self.config.get("model_path", "") or "")
        )
        self.context_size = int(self.config.get("context_size", 4096))
        self.threads = int(self.config.get("threads", 0)) or None
        self.max_tokens = int(self.config.get("max_tokens", 320))
        self.model = self.config.get("model") or (
            os.path.basename(self.model_path) if self.model_path else "")
        self._llm = None

    # -- availability ------------------------------------------------

    def probe(self) -> BackendStatus:
        """
        Checks the library and the model FILE. Deliberately does not
        load the model: loading a 7B GGUF costs seconds and gigabytes,
        which is not acceptable for a status indicator that refreshes
        in the UI.
        """
        def status(detail, state=STATE_UNAVAILABLE):
            return BackendStatus(available=False, backend=self.name,
                                  model=self.model, detail=detail, state=state)

        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return status(
                "llama-cpp-python is not installed in this environment",
                STATE_NOT_CONFIGURED)

        if not self.model_path:
            return status(
                "no local model file has been selected yet",
                STATE_NOT_CONFIGURED)

        if not os.path.isfile(self.model_path):
            return status(f"the configured model file is missing: "
                          f"{self.model_path}")

        size_gb = os.path.getsize(self.model_path) / 1e9
        return BackendStatus(
            available=True, backend=self.name,
            model=self.model or os.path.basename(self.model_path),
            detail=f"{self.model_path} ({size_gb:.1f} GB)")

    # -- generation --------------------------------------------------

    def _load(self):
        if self._llm is not None:
            return self._llm
        from llama_cpp import Llama

        kwargs = {"model_path": self.model_path, "n_ctx": self.context_size,
                  "verbose": False}
        if self.threads:
            kwargs["n_threads"] = self.threads
        self._llm = Llama(**kwargs)
        return self._llm

    def explain(self, finding: dict) -> Optional[NarrationResult]:
        status = self.probe()
        if not status.available:
            return None

        try:
            llm = self._load()
            response = llm.create_chat_completion(
                messages=[{"role": "user", "content": build_prompt(finding)}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            text = (
                response["choices"][0]["message"]["content"] or ""
            ).strip()
        except Exception:
            return None

        if not text:
            return None

        return NarrationResult(
            text=text,
            backend=self.name,
            model=self.model or os.path.basename(self.model_path),
            is_mock=False,
        )

    def close(self) -> None:
        self._llm = None
