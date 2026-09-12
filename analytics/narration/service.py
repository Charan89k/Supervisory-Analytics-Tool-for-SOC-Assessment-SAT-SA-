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

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from analytics.narration import create_backend
from analytics.narration.prompt import validate_explanation
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


#: Fields a backend is allowed to see. Everything a finding carries that
#: an explanation could legitimately draw on, and nothing else.
NARRATION_INPUT_FIELDS = (
    "finding_type", "rule_id", "soc_id", "severity", "alert_id", "case_id",
    "assigned_analyst_id", "rationale", "evidence", "queue_rank",
    "case_rank", "queue_priority", "case_priority", "case_finding_count",
    "finding_id", "capability_area", "selection_reason",
)

#: Context the finding itself does not carry, merged into the view by
#: `build_context` below: which organisation and peer group the entity
#: is, where it ranks, what else landed on the same alert, and how
#: complete its records are. All of it is already computed by the
#: deterministic engine — it was simply never offered to the model,
#: which is why explanations could only paraphrase the rule rationale.
NARRATION_CONTEXT_FIELDS = (
    "organization_name", "peer_group", "entity_risk_score",
    "entity_risk_percentile", "entity_priority_rank",
    "entity_execution_gap_count", "entity_negative_space_count",
    "evidence_completeness", "case_sibling_findings",
)


def build_context(results: Optional[dict], review_queue: Optional[List[dict]] = None
                   ) -> Dict[str, dict]:
    """
    Per-entity explanation context, keyed by soc_id.

    Read-only: it consults a finished assessment and computes nothing
    of its own. Returns an empty mapping when there is no assessment to
    read, so every caller can pass the result straight through without
    checking.
    """
    context: Dict[str, dict] = {}
    for entity in ((results or {}).get("entities") or []):
        soc_id = entity.get("soc_id")
        if not soc_id:
            continue
        completeness = entity.get("evidence_completeness") or {}
        context[str(soc_id)] = {
            "organization_name": entity.get("organization_name"),
            "peer_group": entity.get("peer_group"),
            "entity_risk_score": entity.get("supervisory_risk_score"),
            "entity_risk_percentile": entity.get(
                "supervisory_risk_score_percentile"),
            "entity_priority_rank": entity.get("priority_rank"),
            "entity_execution_gap_count": entity.get("execution_gap_count"),
            "entity_negative_space_count": entity.get("negative_space_count"),
            "evidence_completeness": {
                "overall_coverage": completeness.get("overall_coverage"),
                "limited": completeness.get("limited"),
                "suppressed_rules": completeness.get("suppressed_rules"),
            } if completeness else None,
        }
    return context


def case_siblings(record: dict, review_queue: Optional[List[dict]]) -> List[dict]:
    """
    The other findings correlated onto the same alert as `record`.

    One alert carrying four separate failures is a different supervisory
    conversation from one carrying a single late acknowledgement, and
    the finding on its own cannot express that.
    """
    if not review_queue:
        return []
    case_rank = record.get("case_rank")
    if case_rank is None:
        return []

    return [
        {"finding_type": other.get("finding_type"),
         "rule_id": other.get("rule_id")}
        for other in review_queue
        if other is not record and other.get("case_rank") == case_rank
        and other.get("finding_type")
    ]


def finding_view(record: dict, context: Optional[dict] = None) -> dict:
    """
    A defensive copy of one finding, for handing to a backend.

    A backend receives data, never the authoritative record. Passing the
    live dict meant a backend that mutated its argument — through a bug,
    or deliberately — could rewrite a severity, a score, or the evidence
    itself, and the change would land straight in the assessment the
    supervisor acts on. The service could promise only that IT wrote
    nothing but narration fields; it could not promise the record came
    back unchanged.

    The copy closes that. `evidence` is deep-copied because it is a
    nested dict and a shallow copy would still share it. Whatever a
    backend does to what it is given, the assessment is untouched.
    """
    view = {}
    for field in NARRATION_INPUT_FIELDS:
        if field not in record:
            continue
        value = record[field]
        view[field] = deepcopy(value) if isinstance(value, (dict, list)) else value

    # Context is copied too, and only for keys the contract names, so a
    # caller cannot widen what a backend sees by passing extra keys.
    for field in NARRATION_CONTEXT_FIELDS:
        if not context or field not in context:
            continue
        value = context[field]
        if value is None:
            continue
        view[field] = deepcopy(value) if isinstance(value, (dict, list)) else value

    return view


@dataclass
class NarrationOutcome:
    """What actually happened, for honest reporting in the UI."""

    status: BackendStatus
    considered: int = 0
    explained: int = 0
    failed: int = 0
    skipped_out_of_scope: int = 0
    cancelled: bool = False
    #: Why the most recent explanation was rejected, when one was.
    last_error: str = ""

    def summary(self) -> str:
        if not self.status.available:
            return f"AI unavailable — {self.status.detail}. " \
                   f"{self.considered} finding(s) kept their rule rationale."
        source = "sample explanations" if self.status.is_mock \
            else f"{self.status.model}"
        parts = [f"{self.explained} of {self.considered} review-queue "
                 f"item(s) explained using {source}"]
        if self.failed:
            failed = f"{self.failed} failed and kept the rule rationale"
            # Without the reason, "0 explained; 1 failed" is a dead end
            # for anyone trying to fix it.
            if self.last_error:
                failed += f" ({self.last_error})"
            parts.append(failed)
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
    results: Optional[dict] = None,
) -> NarrationOutcome:
    """
    Add explanations to `review_queue` IN PLACE and report what happened.

    `results` is the finished assessment. Supplying it lets each
    explanation see the entity context the finding alone cannot carry —
    organisation, peer group, risk position, evidence completeness —
    and it is read, never written. Omit it and explanations still work
    from the finding and its evidence.

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

    entity_context = build_context(results)
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

        # The backend sees a copy, enriched with read-only context.
        # Anything it does to that copy is discarded; only the fields
        # written below reach the record.
        view = finding_view(record, entity_context.get(str(record.get("soc_id"))))
        siblings = case_siblings(record, review_queue)
        if siblings:
            view["case_sibling_findings"] = siblings

        result = backend.explain(view)

        # A model that returned nothing usable is a failure, not an
        # explanation. Writing a truncation or an echoed instruction
        # into the record would put text beside a finding that reads as
        # analysis and is not.
        rejection = ""
        if result is not None:
            usable, rejection = validate_explanation(result.text, view)
            if not usable:
                result = None

        if result is None:
            outcome.failed += 1
            # A rejection is the model answering unusably; last_failure
            # is it not answering at all. Either way the supervisor is
            # owed the reason, not just the count.
            outcome.last_error = (
                rejection or getattr(backend, "last_failure", "")
                or outcome.last_error)
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
