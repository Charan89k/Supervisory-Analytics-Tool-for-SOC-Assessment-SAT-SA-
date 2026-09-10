"""
Compatibility shim for the pre-Phase-3 narration entry points.

The real implementation now lives in `analytics.narration`, which
supports pluggable backends (llama.cpp for the shipped air-gapped
product, Ollama for development, a deterministic mock for tests) and
bounded scope selection. This module keeps `narrate_finding` and
`narrate_queue` working for `main.py --narrate` and for anything that
imported them directly.

The architectural rule is unchanged and enforced in the new package:
narration restates findings the deterministic engine already decided.
It never creates, removes, re-scores, or re-ranks anything.
"""

from typing import Optional

from analytics.narration import create_backend
from analytics.narration.base import LocalLLMBackend
from analytics.narration.service import (
    DEFAULT_MAX_EXPLANATIONS,
    NarrationScope,
    narrate_review_queue,
)

__all__ = ["narrate_finding", "narrate_queue"]


def narrate_finding(finding: dict, cfg: dict,
                     backend: Optional[LocalLLMBackend] = None) -> str:
    """
    Narrated explanation for one finding, or the rule-generated
    rationale when narration is disabled, unavailable, or fails.
    """
    fallback = finding.get("rationale", "")

    if not (cfg.get("llm_narration", {}) or {}).get("enabled", False):
        return fallback

    backend = backend or create_backend(cfg)
    if not backend.probe().available:
        return fallback

    result = backend.explain(finding)
    return result.text if result is not None else fallback


def narrate_queue(review_queue_records: list, cfg: dict,
                   backend: Optional[LocalLLMBackend] = None) -> list:
    """
    Add `narrated_explanation` to each review-queue record, in place,
    leaving every other field untouched.

    Scope comes from `llm_narration` in the config, so a CLI run
    honours the same bounded-work limits as the desktop application
    rather than quietly sending every finding to the model.
    """
    narration_cfg = cfg.get("llm_narration", {}) or {}

    scope = NarrationScope(
        enabled=bool(narration_cfg.get("enabled", False)),
        max_explanations=int(
            narration_cfg.get("max_explanations", DEFAULT_MAX_EXPLANATIONS)),
        severity_scope=str(
            narration_cfg.get("severity_scope", "critical_high")),
        max_queue_rank=narration_cfg.get("max_queue_rank"),
    )

    narrate_review_queue(review_queue_records, cfg, scope=scope,
                          backend=backend)
    return review_queue_records
