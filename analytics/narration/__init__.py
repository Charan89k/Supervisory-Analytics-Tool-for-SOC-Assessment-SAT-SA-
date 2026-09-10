"""
SAT-SA local explanation layer.

Backend selection lives here so that nothing above this package knows
which inference path is in use:

    LocalLLMBackend
    ├── LlamaCppBackend   (shipped: local .gguf, no service)
    ├── OllamaBackend     (development: local Ollama daemon)
    └── MockBackend       (tests/demo: deterministic samples, no model)

Resolution order, most specific first:

    1. SATSA_NARRATION_BACKEND environment variable
    2. llm_narration.backend in assessment_rules.yaml
    3. "mock"

The environment variable exists so automated tests and UI smoke runs
can force the mock backend without editing config or risking a real
model being spun up on a developer machine. Air-gapped deployments
set the config key and never touch the variable.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Type

from analytics.narration.base import (
    BackendStatus,
    LocalLLMBackend,
    NarrationResult,
    build_prompt,
)
from analytics.narration.llamacpp_backend import LlamaCppBackend
from analytics.narration.mock import MockBackend
from analytics.narration.ollama_backend import OllamaBackend

BACKENDS: Dict[str, Type[LocalLLMBackend]] = {
    "mock": MockBackend,
    "ollama": OllamaBackend,
    "llamacpp": LlamaCppBackend,
}

#: Forcing this to "mock" makes every narration path in the application
#: run without loading a language model.
ENV_BACKEND = "SATSA_NARRATION_BACKEND"

DEFAULT_BACKEND = "mock"

__all__ = [
    "BACKENDS", "BackendStatus", "DEFAULT_BACKEND", "ENV_BACKEND",
    "LlamaCppBackend", "LocalLLMBackend", "MockBackend", "NarrationResult",
    "OllamaBackend", "build_prompt", "create_backend", "resolve_backend_name",
]


def resolve_backend_name(cfg: Optional[dict] = None) -> str:
    """Which backend key applies, honouring the environment override."""
    override = os.environ.get(ENV_BACKEND, "").strip().lower()
    if override in BACKENDS:
        return override

    narration_cfg = (cfg or {}).get("llm_narration", {}) or {}
    configured = str(narration_cfg.get("backend", "")).strip().lower()
    if configured in BACKENDS:
        return configured

    return DEFAULT_BACKEND


def create_backend(cfg: Optional[dict] = None) -> LocalLLMBackend:
    """
    Build the configured backend. Never raises on an unknown name — an
    assessment must not fail because of a typo in an optional layer;
    it falls back to the mock explainer, which announces itself.
    """
    narration_cfg = dict((cfg or {}).get("llm_narration", {}) or {})
    name = resolve_backend_name(cfg)
    return BACKENDS.get(name, MockBackend)(narration_cfg)
