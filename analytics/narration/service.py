"""
Narration orchestration — decides WHICH findings get explained, and
adds the explanations without touching anything else.

Two rules govern this module:

  1. Narration is additive. It writes `narrated_explanation` and its
     provenance fields onto a record and changes nothing else. No
     finding_type, severity, score, rank, or evidence value is ever
     modified here. Remove this module entirely and the assessment is
     identical in substance.

  2. Scope is bounded by default. The current dataset produces ~1,480
     findings; explaining all of them on CPU at minutes per call is
     hours of compute for output a supervisor will never read. The
     review queue already ranks findings by supervisory priority, so
     narration follows that ranking and stops at a limit.

Note on scoping by severity: the review queue is ALREADY effectively
all-CRITICAL/HIGH (81 of 81 on the current dataset), because queue
priority is finding weight x severity boost. Filtering the queue by
severity is therefore close to a no-op, and cannot be the mechanism
that keeps a run short. Queue rank and an explicit maximum are the
levers that actually bound the work, so those are the defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from analytics.narration import create_backend
from analytics.narration.base import BackendStatus, LocalLLMBackend

#: Practical default for a CPU-only laptop. At 1-4 minutes per call on
#: a 7B model this is a coffee break, not an afternoon.
DEFAULT_MAX_EXPLANATIONS = 10

SEVERITY_SCOPES = {
    "critical": ["CRITICAL"],
    "critical_high": ["CRITICAL", "HIGH"],
    "critical_high_medium": ["CRITICAL", "HIGH", "MEDIUM"],
    "all": None,  # no severity filter
}


@dataclass
class NarrationScope:
    """Which findings to explain, and how many."""

    enabled: bool = True
    max_explanations: int = DEFAULT_MAX_EXPLANATIONS
    #: Key into SEVERITY_SCOPES.
    severity_scope: str = "critical_high"
    #: Only explain queue items ranked at or above this cut-off.
    #: None means "no rank limit" (max_explanations still applies).
    max_queue_rank: Optional[int] = None

    def severities(self) -> Optional[List[str]]:
        return SEVERITY_SCOPES.get(self.severity_scope, ["CRITICAL", "HIGH"])


@dataclass
class NarrationOutcome:
    """What actually happened, for honest reporting in the UI."""

    status: BackendStatus
    considered: int = 0
    explained: int = 0
    failed: int = 0
    skipped_out_of_scope: int = 0
    cancelled: bool = False

    def summary(self) -> str:
        if not self.status.available:
            return f"AI unavailable — {self.status.detail}. " \
                   f"{self.considered} finding(s) kept their rule rationale."
        source = "sample explanations" if self.status.is_mock \
            else f"{self.status.model}"
        parts = [f"{self.explained} of {self.considered} review-queue "
                 f"item(s) explained using {source}"]
        if self.failed:
            parts.append(f"{self.failed} failed and kept the rule rationale")
        if self.cancelled:
            parts.append("run cancelled")
        return "; ".join(parts) + "."


def select_for_narration(review_queue: Sequence[dict],
                          scope: NarrationScope) -> List[dict]:
    """
    Pick the records to explain, in queue order. Pure and deterministic
    so the selection itself is auditable — a supervisor can see exactly
    which items were sent and why.
    """
    severities = scope.severities()
    selected = []

    for record in review_queue:
        if scope.max_queue_rank is not None:
            rank = record.get("queue_rank")
            if rank is not None and rank > scope.max_queue_rank:
                continue

        if severities is not None:
            severity = str(record.get("severity") or "").upper()
            if severity not in severities:
                continue

        selected.append(record)
        if len(selected) >= scope.max_explanations:
            break

    return selected


def narrate_review_queue(
    review_queue: List[dict],
    cfg: dict,
    scope: Optional[NarrationScope] = None,
    backend: Optional[LocalLLMBackend] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> NarrationOutcome:
    """
    Add explanations to `review_queue` IN PLACE and report what happened.

    Every record — explained or not — ends up with a
    `narrated_explanation`. Records that were not explained get their
    deterministic rationale, so downstream consumers never have to
    handle a missing field, and a report never has a blank where an
    explanation should be.
    """
    scope = scope or NarrationScope()
    backend = backend or create_backend(cfg)
    status = backend.probe()

    # Baseline: the rule rationale, marked as not model-generated.
    for record in review_queue:
        record.setdefault("narrated_explanation", record.get("rationale", ""))
        record.setdefault("narration_source", "rule")
        record.setdefault("narration_is_mock", False)

    outcome = NarrationOutcome(status=status, considered=len(review_queue))

    if not scope.enabled or not status.available:
        return outcome

    selected = select_for_narration(review_queue, scope)
    outcome.skipped_out_of_scope = len(review_queue) - len(selected)
    total = len(selected)

    for index, record in enumerate(selected):
        if should_cancel is not None and should_cancel():
            outcome.cancelled = True
            break

        result = backend.explain(record)
        if result is None:
            outcome.failed += 1
        else:
            record["narrated_explanation"] = result.text
            record["narration_source"] = result.backend
            record["narration_model"] = result.model
            record["narration_is_mock"] = result.is_mock
            record["narration_provenance"] = result.provenance()
            outcome.explained += 1

        if progress_callback is not None:
            progress_callback(index + 1, total)

    return outcome
