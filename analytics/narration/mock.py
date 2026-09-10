"""
Built-in sample explainer — the test and demo backend.

Exists so the full narration pipeline (scope selection, worker
threading, progress reporting, provenance, report rendering) can be
exercised on any machine without executing a 3B-14B parameter model.
A developer laptop should not have to spend minutes of CPU and ~9 GB
of RAM to answer "does the progress bar update correctly?".

It is NOT a stand-in for Qwen and never presents itself as one:

  * every result carries is_mock=True, model="deterministic-sample"
  * `provenance()` states plainly that no language model was involved
  * the text itself opens with "[SAMPLE EXPLANATION]"

That labelling is not decoration. This is a supervisory tool whose
output feeds regulatory assessment; text that reads as model-generated
analysis but was produced by a template would misrepresent the basis
of a finding. The label travels with the text into the UI and every
exported report.

The explanations are templated from the finding's OWN deterministic
fields — rule rationale, evidence, severity — so they are realistic
and specific to the finding, and they add no claim the rule engine did
not already make.
"""

from __future__ import annotations

from typing import Optional

from analytics.narration.base import (
    BackendStatus,
    LocalLLMBackend,
    NarrationResult,
)

#: What a supervisor should do next, per rule. Deterministic, auditable,
#: and written from the same evidence the rule already recorded.
REVIEW_STEPS = {
    "MISSED_ESCALATION": (
        "pull the escalation policy in force during the period and confirm "
        "whether an out-of-band escalation happened that was never recorded"),
    "SLOW_TRIAGE": (
        "check staffing and queue depth at the time of the alert before "
        "treating the delay as an individual performance issue"),
    "FAST_CLOSURE": (
        "read the investigation notes for this alert and judge whether the "
        "work described could have been completed in the time recorded"),
    "MISSING_EVIDENCE": (
        "ask the entity where evidence for this closure is retained, since "
        "absence here may reflect a storage practice rather than no work"),
    "REOPENED_CASE": (
        "compare the first and second closure reasons to see whether the "
        "reopening reflects new information or an incomplete first pass"),
    "REPETITIVE_INVESTIGATION": (
        "sample several of these identically-noted cases and check whether "
        "the underlying alerts genuinely warranted the same conclusion"),
    "ANALYST_OVERLOAD": (
        "review shift rosters and assignment rules for the period rather "
        "than the analyst's individual output"),
    "ACK_WITHOUT_INVESTIGATION": (
        "confirm whether investigation activity for this alert was recorded "
        "in a system outside this submission before drawing a conclusion"),
    "REPEATED_ALERT_WITHOUT_REMEDIATION": (
        "ask the entity what remediation was performed on this asset and "
        "why the recurrence continued afterwards"),
    "TELEMETRY_GAP": (
        "establish whether this source is genuinely expected for this "
        "environment, and if so when coverage was last verified"),
    "MISSING_ALERT_CATEGORY": (
        "check whether the entity operates tooling for this threat class "
        "at all before treating the absence as a monitoring gap"),
    "LOW_ACTIVITY_OUTLIER": (
        "compare the entity's monitored estate size against its peers "
        "before concluding that low volume indicates a blind spot"),
    "MISSING_ESCALATION_RECORDS": (
        "request the entity's escalation register directly, since coverage "
        "this low usually means records are kept somewhere else"),
    "MISSING_INVESTIGATIONS": (
        "ask where investigation write-ups are retained and whether this "
        "submission was expected to include them"),
}

DEFAULT_STEP = ("trace this finding back to its underlying records and confirm "
                "the evidence against the entity's own documentation")


class MockBackend(LocalLLMBackend):
    """Deterministic sample explainer. Always available, runs no model."""

    name = "mock"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.model = "deterministic-sample"

    def probe(self) -> BackendStatus:
        return BackendStatus(
            available=True,
            backend=self.name,
            model=self.model,
            detail="built-in sample explainer; no language model is running",
            is_mock=True,
        )

    def explain(self, finding: dict) -> Optional[NarrationResult]:
        finding_type = str(finding.get("finding_type", "UNKNOWN"))
        severity = finding.get("severity") or "unrated"
        soc_id = finding.get("soc_id", "this entity")
        rationale = str(finding.get("rationale", "")).strip()
        evidence = finding.get("evidence") or {}

        if isinstance(evidence, dict) and evidence:
            evidence_text = ", ".join(
                f"{key.replace('_', ' ')} = {value}"
                for key, value in list(evidence.items())[:5]
            )
        else:
            evidence_text = "no structured evidence was recorded for this finding"

        step = REVIEW_STEPS.get(finding_type, DEFAULT_STEP)

        text = (
            f"[SAMPLE EXPLANATION] Rule {finding.get('rule_id', 'UNKNOWN')} "
            f"({finding_type}) flagged {soc_id} at {severity} severity. "
            f"{rationale} "
            f"The rule recorded: {evidence_text}. "
            f"This is an indicator for supervisory review, not a determination "
            f"of wrongdoing. Suggested next step: {step}."
        )

        return NarrationResult(
            text=text,
            backend=self.name,
            model=self.model,
            is_mock=True,
        )
