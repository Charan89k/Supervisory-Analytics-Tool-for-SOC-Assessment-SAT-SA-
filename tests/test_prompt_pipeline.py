"""
The explanation prompt: real finding in, finding-specific prompt out.

The defect these defend against was quiet. `finding_view()` assembled
fourteen fields for the model and `build_prompt()` formatted six of
them, dropping the alert id, the case id, the analyst and every ranking
field. Nothing failed; the model simply never learned which alert it
was describing, so it could only paraphrase the rule's own rationale
back and every explanation read the same.

So these tests assert what actually reaches the model, not merely that
a prompt was produced.
"""

import pytest

from analytics.narration.prompt import (
    MIN_EXPLANATION_CHARS,
    build_explanation_prompt,
    validate_explanation,
)
from analytics.narration.service import (
    NarrationScope,
    build_context,
    case_siblings,
    finding_view,
    narrate_review_queue,
)


def finding(**overrides):
    base = {
        "finding_id": "F-001",
        "finding_type": "SLOW_TRIAGE",
        "rule_id": "ACK-SLA-001",
        "severity": "HIGH",
        "soc_id": "SOC-007",
        "alert_id": "ALT-SOC-007-000123",
        "case_id": "CASE-ALT-SOC-007-000123",
        "assigned_analyst_id": "ANL-SOC-007-002",
        "rationale": "Acknowledgement took 82.8 minutes, exceeding 2.5x the "
                     "15-minute SLA target for HIGH severity.",
        "evidence": {"ack_delay_minutes": 82.8, "sla_target_minutes": 15,
                      "threshold_minutes": 37.5, "severity": "HIGH"},
        "queue_rank": 3,
        "case_rank": 2,
        "queue_priority": 1.5,
        "case_priority": 6.0,
        "case_finding_count": 4,
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# The prompt carries the actual finding
# ----------------------------------------------------------------------

@pytest.mark.parametrize("needle", [
    "F-001",                       # finding id
    "SLOW_TRIAGE",                 # finding type
    "ACK-SLA-001",                 # detector / rule
    "SOC-007",                     # entity
    "ALT-SOC-007-000123",          # alert
    "CASE-ALT-SOC-007-000123",     # case
    "ANL-SOC-007-002",             # analyst
    "82.8",                        # an actual evidence value
    "sla_target_minutes",          # an actual evidence key
])
def test_the_prompt_carries_the_real_finding(needle):
    assert needle in build_explanation_prompt(finding())


def test_the_prompt_carries_the_rule_rationale():
    assert "exceeding 2.5x the" in build_explanation_prompt(finding())


def test_the_prompt_carries_supervisory_priority():
    prompt = build_explanation_prompt(finding())
    assert "Position in the supervisory review queue: 3" in prompt
    assert "Findings correlated onto this same alert: 4" in prompt


def test_every_evidence_key_reaches_the_prompt():
    """
    The regression itself: fields were copied for the model and then
    dropped before it saw them.
    """
    record = finding()
    prompt = build_explanation_prompt(record)
    for key, value in record["evidence"].items():
        assert key in prompt, key
        assert str(value) in prompt, value


# ----------------------------------------------------------------------
# Different findings must produce different prompts
# ----------------------------------------------------------------------

def test_different_findings_produce_different_prompts():
    a = build_explanation_prompt(finding())
    b = build_explanation_prompt(finding(
        finding_id="F-002", finding_type="MISSED_ESCALATION",
        rule_id="ESC-REQUIRED-001", severity="CRITICAL",
        alert_id="ALT-SOC-007-000999", case_id="CASE-ALT-SOC-007-000999",
        rationale="Escalation was required but none was initiated.",
        evidence={"escalation_required": True, "escalation_initiated": False}))

    assert a != b
    assert "ALT-SOC-007-000123" in a and "ALT-SOC-007-000123" not in b
    assert "ALT-SOC-007-000999" in b and "ALT-SOC-007-000999" not in a
    assert "escalation_initiated" in b and "escalation_initiated" not in a
    assert "ACK-SLA-001" in a and "ESC-REQUIRED-001" in b


def test_the_same_finding_always_produces_the_same_prompt():
    """Deterministic, so a prompt can be reproduced and audited."""
    record = finding()
    first = build_explanation_prompt(record)
    for _ in range(3):
        assert build_explanation_prompt(record) == first


# ----------------------------------------------------------------------
# Absent data is represented as absent, never invented
# ----------------------------------------------------------------------

def test_a_finding_with_no_evidence_says_so():
    prompt = build_explanation_prompt(finding(evidence={}))
    assert "No structured evidence was attached" in prompt


def test_absent_fields_are_omitted_not_blanked():
    """
    A blank next to a label is something a model can narrate as fact.
    Omitting the line cannot be misread.
    """
    sparse = {"finding_type": "TELEMETRY_GAP", "rule_id": "TELEMETRY-COVERAGE-001",
              "soc_id": "SOC-001", "evidence": {"coverage_percentage": 39.6}}
    prompt = build_explanation_prompt(sparse)

    assert "Alert ID" not in prompt
    assert "Assigned analyst" not in prompt
    assert "None" not in prompt.split("=== YOUR TASK ===")[0]
    assert "39.6" in prompt


def test_null_valued_fields_are_treated_as_absent():
    prompt = build_explanation_prompt(finding(alert_id=None, case_id="nan"))
    assert "Alert ID" not in prompt
    assert "Case ID" not in prompt


def test_the_prompt_forbids_inventing_evidence():
    prompt = build_explanation_prompt(finding())
    lowered = prompt.lower()
    for rule in ("never invent evidence, alert ids, case ids, analyst ids, "
                  "timestamps, counts or metrics",
                  "never change or restate a different severity",
                  "never create, merge, split or dismiss a finding",
                  "never contradict or override",
                  "missing evidence",
                  "insufficient"):
        assert rule in lowered, rule


def test_the_prompt_separates_missing_evidence_from_failure():
    lowered = build_explanation_prompt(finding()).lower()
    assert "does not prove the activity never happened" in lowered
    assert "indicators for supervisory review" in lowered


# ----------------------------------------------------------------------
# Context enrichment
# ----------------------------------------------------------------------

def test_entity_context_reaches_the_prompt():
    results = {"entities": [{
        "soc_id": "SOC-007", "organization_name": "Energy CSE 007",
        "peer_group": "ENERGY", "supervisory_risk_score": 91.5,
        "supervisory_risk_score_percentile": 100.0, "priority_rank": 2,
        "execution_gap_count": 40, "negative_space_count": 3,
        "evidence_completeness": {"overall_coverage": 0.42, "limited": True,
                                   "suppressed_rules": ["MISSED_ESCALATION"]},
    }]}
    view = finding_view(finding(), build_context(results)["SOC-007"])
    prompt = build_explanation_prompt(view)

    assert "Energy CSE 007" in prompt
    assert "ENERGY" in prompt
    assert "91.5" in prompt
    assert "42%" in prompt
    assert "MISSED_ESCALATION" in prompt
    assert "floor, not a measurement" in prompt


def test_case_siblings_reach_the_prompt():
    queue = [finding(),
             finding(finding_id="F-002", finding_type="MISSING_EVIDENCE",
                      rule_id="EVIDENCE-REQUIRED-001"),
             finding(finding_id="F-003", finding_type="FAST_CLOSURE",
                      rule_id="TRIAGE-FAST-001"),
             finding(finding_id="F-099", case_rank=99)]

    siblings = case_siblings(queue[0], queue)
    assert {s["finding_type"] for s in siblings} == {"MISSING_EVIDENCE",
                                                      "FAST_CLOSURE"}

    view = finding_view(queue[0])
    view["case_sibling_findings"] = siblings
    prompt = build_explanation_prompt(view)
    assert "OTHER FINDINGS ON THIS SAME ALERT" in prompt
    assert "MISSING_EVIDENCE" in prompt


def test_context_cannot_widen_what_the_backend_sees():
    """A caller cannot smuggle extra keys in through context."""
    view = finding_view(finding(), {"peer_group": "ENERGY",
                                     "secret_internal_field": "leaked"})
    assert view.get("peer_group") == "ENERGY"
    assert "secret_internal_field" not in view


def test_build_context_on_an_empty_assessment():
    assert build_context(None) == {}
    assert build_context({}) == {}
    assert case_siblings(finding(), None) == []


# ----------------------------------------------------------------------
# Response validation
# ----------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, "", "   ", "\n\n", "too short"])
def test_unusable_responses_are_rejected(bad):
    ok, reason = validate_explanation(bad)
    assert ok is False
    assert reason


def test_an_echoed_prompt_is_rejected():
    ok, reason = validate_explanation(
        "You are the explanation assistant for SAT-SA, a supervisory "
        "analytics tool used by an examiner to review how a Critical "
        "Sector Entity handled its alerts and so on and so forth.")
    assert ok is False
    assert "echoed" in reason


def test_a_real_explanation_is_accepted():
    ok, reason = validate_explanation(
        "Finding: alert ALT-SOC-007-000123 was acknowledged 82.8 minutes "
        "after creation, against a 15-minute target. Evidence: "
        "ack_delay_minutes 82.8. Why it matters: the entity's triage "
        "process may not be meeting its own SLA. Recommended supervisory "
        "review: sample other HIGH alerts from the same shift.")
    assert ok is True
    assert reason == ""
    assert len("x" * MIN_EXPLANATION_CHARS) == MIN_EXPLANATION_CHARS


# ----------------------------------------------------------------------
# Failure never touches the deterministic finding
# ----------------------------------------------------------------------

class _Backend:
    """A stub local backend. No model, no network."""

    name = "stub"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.prompts_seen = []

    def probe(self):
        from analytics.narration.base import STATE_AVAILABLE, BackendStatus
        return BackendStatus(available=True, backend=self.name,
                              model="stub-model", state=STATE_AVAILABLE)

    def explain(self, finding):
        from analytics.narration.base import NarrationResult, build_prompt
        self.prompts_seen.append(build_prompt(finding))

        if self.behaviour == "none":
            return None
        if self.behaviour == "empty":
            text = ""
        elif self.behaviour == "short":
            text = "ok"
        else:
            text = ("Finding: a real, sufficiently long explanation of the "
                    "supplied finding for the supervisor to read and act on.")
        return NarrationResult(text=text, backend=self.name,
                                model="stub-model", is_mock=False)


def _queue():
    return [finding(), finding(finding_id="F-002", alert_id="ALT-B",
                                case_rank=9)]


@pytest.mark.parametrize("behaviour", ["none", "empty", "short"])
def test_a_failing_model_leaves_the_findings_untouched(behaviour):
    queue = _queue()
    before = [{k: v for k, v in r.items()} for r in queue]

    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=5),
        backend=_Backend(behaviour))

    assert outcome.explained == 0
    assert outcome.failed == 2

    for original, after in zip(before, queue):
        for field in ("finding_type", "rule_id", "severity", "soc_id",
                       "alert_id", "evidence", "queue_priority",
                       "case_priority", "rationale"):
            assert after[field] == original[field], field
        # The deterministic rationale is what remains readable.
        assert after["narrated_explanation"] == original["rationale"]
        assert after["narration_source"] == "rule"


def test_a_rejected_response_is_reported_not_stored():
    queue = _queue()
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=5),
        backend=_Backend("short"))

    assert outcome.failed == 2
    assert "too short" in outcome.last_error
    assert all(r["narration_source"] == "rule" for r in queue)


def test_a_good_response_is_stored_with_provenance():
    queue = _queue()
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=5),
        backend=_Backend("good"))

    assert outcome.explained == 2
    for record in queue:
        assert record["narration_source"] == "stub"
        assert record["narration_is_mock"] is False
        assert "stub-model" in record["narration_provenance"]


def test_each_finding_gets_its_own_prompt_end_to_end():
    backend = _Backend("good")
    narrate_review_queue(_queue(), {},
                          scope=NarrationScope(max_explanations=5),
                          backend=backend)

    assert len(backend.prompts_seen) == 2
    assert backend.prompts_seen[0] != backend.prompts_seen[1]
    assert "ALT-SOC-007-000123" in backend.prompts_seen[0]
    assert "ALT-B" in backend.prompts_seen[1]


# ----------------------------------------------------------------------
# The model cannot change the assessment
# ----------------------------------------------------------------------

def test_a_hostile_backend_changes_nothing():
    """
    Structural, not advisory: the backend is handed a copy, and the
    service writes back only narration fields.
    """
    from analytics.narration.base import NarrationResult

    class Hostile(_Backend):
        def explain(self, finding):
            finding["severity"] = "LOW"
            finding["queue_priority"] = 0.0
            finding["case_priority"] = 0.0
            finding["evidence"] = {"fabricated": True}
            finding["rule_id"] = "MADE-UP-001"
            finding["finding_type"] = "INVENTED"
            return NarrationResult(
                text="Finding: a plausible looking explanation that is long "
                     "enough to pass validation but should change nothing.",
                backend=self.name, model="stub-model", is_mock=False)

    queue = _queue()
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=5),
                          backend=Hostile("good"))

    first = queue[0]
    assert first["severity"] == "HIGH"
    assert first["queue_priority"] == 1.5
    assert first["case_priority"] == 6.0
    assert first["rule_id"] == "ACK-SLA-001"
    assert first["finding_type"] == "SLOW_TRIAGE"
    assert first["evidence"]["ack_delay_minutes"] == 82.8
    assert "fabricated" not in first["evidence"]


def test_a_backend_cannot_add_or_remove_findings():
    class Greedy(_Backend):
        def explain(self, finding):
            finding["extra_finding"] = {"finding_type": "INVENTED"}
            return super().explain(finding)

    queue = _queue()
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=5),
                          backend=Greedy("good"))

    assert len(queue) == 2
    assert all("extra_finding" not in r for r in queue)


def test_only_narration_fields_are_ever_written():
    queue = [finding()]
    before = set(queue[0])
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=5),
                          backend=_Backend("good"))

    added = set(queue[0]) - before
    assert added == {"narrated_explanation", "narration_source",
                     "narration_model", "narration_is_mock",
                     "narration_provenance"}
