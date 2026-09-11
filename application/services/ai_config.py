"""
Desktop-facing AI settings.

Translates the supervisor-visible choices (on/off, Fast/Balanced/Deep,
scope) into the `llm_narration` config dict the analytics narration
layer consumes. The UI never talks to a backend directly.
"""

from dataclasses import dataclass, field
from typing import Optional

from analytics.narration import DEFAULT_BACKEND
from analytics.narration.service import DEFAULT_MAX_EXPLANATIONS

#: Supervisor-facing performance modes.
#:
#: 14B is offered but never defaulted to: it needs roughly 9 GB resident
#: and is unusable on modest hardware. Presenting it as a deliberate
#: "Deep" choice is honest; making it the default would not be.
MODELS = {
    "fast": "qwen2.5:3b",
    "balanced": "qwen2.5:7b",
    "deep": "qwen2.5:14b",
}

MODE_LABELS = {
    "fast": "Fast — Qwen 2.5 3B",
    "balanced": "Balanced — Qwen 2.5 7B",
    "deep": "Deep — Qwen 2.5 14B",
}

#: Scope keys understood by analytics.narration.service.
SEVERITY_SCOPES = {
    "critical": "Critical only",
    "critical_high": "Critical + High",
    "critical_high_medium": "Critical + High + Medium",
    "all": "All eligible findings",
}


@dataclass
class AIConfig:
    """Configuration for SAT-SA's optional local AI explanation layer."""

    enabled: bool = False

    #: "ollama" (default, zero-configuration), "llamacpp" (strict
    #: air-gap), "mock" (tests/demo).
    backend: str = DEFAULT_BACKEND
    mode: str = "balanced"
    model: str = MODELS["balanced"]

    #: Path to a local .gguf file. Used ONLY by LlamaCppBackend, the
    #: strict air-gap path. The default Ollama path discovers its own
    #: runtime and model, so this stays empty and the settings screen
    #: does not ask for it — it is deployment configuration and
    #: diagnostics, not a supervisor-facing choice.
    model_path: str = ""

    severity_scope: str = "critical_high"
    max_explanations: int = DEFAULT_MAX_EXPLANATIONS
    max_queue_rank: Optional[int] = None

    temperature: float = 0.1
    timeout: int = 180
    availability_timeout: int = 3

    def to_narration_config(self) -> dict:
        """The `llm_narration` block the analytics layer expects."""
        return {
            "llm_narration": {
                "enabled": self.enabled,
                "backend": self.backend,
                "model": self.model,
                "model_path": self.model_path,
                "temperature": self.temperature,
                "timeout_seconds": self.timeout,
                "availability_timeout_seconds": self.availability_timeout,
                "max_explanations": self.max_explanations,
                "severity_scope": self.severity_scope,
                "max_queue_rank": self.max_queue_rank,
            }
        }


def create_ai_config(
    mode: str = "balanced",
    enabled: bool = False,
    backend: str = DEFAULT_BACKEND,
    **overrides,
) -> AIConfig:
    """Build an AIConfig from a performance mode."""
    if mode not in MODELS:
        raise ValueError(
            f"Unknown AI mode: {mode}. Available modes: {', '.join(MODELS)}"
        )

    return AIConfig(
        enabled=enabled,
        backend=backend,
        mode=mode,
        model=MODELS[mode],
        **overrides,
    )


def from_yaml_config(cfg: dict) -> AIConfig:
    """Build an AIConfig from assessment_rules.yaml."""
    narration = (cfg or {}).get("llm_narration", {}) or {}
    model = narration.get("model", MODELS["balanced"])
    mode = next((m for m, name in MODELS.items() if name == model), "balanced")

    return AIConfig(
        enabled=bool(narration.get("enabled", False)),
        backend=str(narration.get("backend", DEFAULT_BACKEND)),
        mode=mode,
        model=model,
        model_path=str(narration.get("model_path", "") or ""),
        severity_scope=str(narration.get("severity_scope", "critical_high")),
        max_explanations=int(
            narration.get("max_explanations", DEFAULT_MAX_EXPLANATIONS)),
        max_queue_rank=narration.get("max_queue_rank"),
        temperature=float(narration.get("temperature", 0.1)),
        timeout=int(narration.get("timeout_seconds", 180)),
        availability_timeout=int(
            narration.get("availability_timeout_seconds", 3)),
    )
