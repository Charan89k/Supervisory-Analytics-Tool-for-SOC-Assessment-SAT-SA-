"""
Prompt construction for the optional explanation layer.

WHY THIS MODULE EXISTS

`finding_view()` assembles fourteen fields for the model. The original
`build_prompt()` formatted six of them and silently dropped the rest —
so the model never learned which alert, which case, which analyst, or
where the finding ranked. It could only ever paraphrase the rule's own
rationale back, which is why explanations read as generic even though
the backend was working correctly.

Everything here is built from the finding the deterministic engine
already decided. Nothing is invented, and a field that is absent is
omitted rather than filled with a placeholder the model might then
narrate as fact.

THE BOUNDARY, RESTATED

The prompt is the only channel from SAT-SA to the model, and it is
one-way. The model returns display text. It cannot reach a finding, a
severity, a score, a rank or an evidence value, because the caller
hands it a defensive copy and writes back only narration fields.
The instructions below are belt-and-braces on top of that structural
guarantee, not a substitute for it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

#: Minimum length before a response is treated as a real explanation
#: rather than a truncation or a refusal.
MIN_EXPLANATION_CHARS = 60

#: What the model must never do. Structural enforcement lives in
#: `service.narrate_review_queue`, which writes back only narration_*
#: fields; this is the instruction half of the same rule.
SYSTEM_RULES = """You are the explanation assistant for SAT-SA, a supervisory analytics tool \
used by an examiner to review how a Critical Sector Entity's Security Operations \
Centre handled its own alerts.

You are NOT the assessment engine. A deterministic rule engine has already decided \
this finding, its severity and its priority. Your only job is to explain that \
decision to a human examiner using the data supplied below.

ABSOLUTE RULES
- Use ONLY the finding and evidence supplied below. Nothing else.
- Never invent evidence, alert IDs, case IDs, analyst IDs, timestamps, counts or metrics.
- Never invent actions an analyst did or did not take beyond what the evidence records.
- Never state that a security incident, breach or compromise occurred. The evidence \
describes how alerts were HANDLED, not whether an attack succeeded.
- Never change or restate a different severity, risk score or review priority.
- Never create, merge, split or dismiss a finding.
- Never contradict or override the rule engine's decision.
- If the supplied evidence is insufficient to explain something, say so plainly \
instead of filling the gap.
- Distinguish carefully between MISSING EVIDENCE and EVIDENCE OF FAILURE. A record \
that is absent means the activity cannot be confirmed either way — it does not prove \
the activity never happened.
- Negative-space findings (absent telemetry, absent categories, absent records) are \
indicators for supervisory review, not proof of non-compliance.
- Write about the entity and its process, never about a named individual's competence."""

#: Section order in the prompt. Only fields actually present are shown,
#: so the model is never handed a blank it might narrate as a fact.
FINDING_FIELDS = [
    ("finding_id", "Finding ID"),
    ("finding_type", "Finding type"),
    ("rule_id", "Rule / detector"),
    ("capability_area", "Capability area"),
    ("severity", "Severity (decided by the rule engine)"),
    ("soc_id", "Entity (CSE)"),
    ("organization_name", "Organisation"),
    ("peer_group", "Sector / peer group"),
    ("alert_id", "Alert ID"),
    ("case_id", "Case ID"),
    ("assigned_analyst_id", "Assigned analyst ID"),
]

RANKING_FIELDS = [
    ("queue_rank", "Position in the supervisory review queue"),
    ("case_rank", "Case rank"),
    ("queue_priority", "Finding priority (weight x severity boost)"),
    ("case_priority", "Case priority (sum across the case)"),
    ("case_finding_count", "Findings correlated onto this same alert"),
]

ENTITY_FIELDS = [
    ("entity_risk_score", "Entity supervisory risk score"),
    ("entity_risk_percentile", "Percentile within its peer group"),
    ("entity_priority_rank", "Entity rank across the submission"),
    ("entity_execution_gap_count", "Total execution-gap findings for this entity"),
    ("entity_negative_space_count", "Total negative-space findings for this entity"),
]

OUTPUT_CONTRACT = """Write the explanation in exactly these four labelled sections, in this order, \
using plain text with no markdown, no bullet characters and no headings other than \
the four labels:

Finding:
What the rule detected, and why it was flagged. Name the specific alert, case or \
entity the finding is about, using the identifiers supplied above.

Evidence:
The specific values from the evidence that support it. Quote the actual numbers, \
states and identifiers given. If something expected is absent, say it is absent and \
say that its absence means the activity cannot be confirmed either way.

Why it matters:
Why this is worth a supervisor's attention, in terms of the entity's process rather \
than any individual's performance. Do not overstate: this is an indicator for review.

Recommended supervisory review:
One or two concrete things the examiner should look at next, derived only from the \
evidence supplied.

Keep the whole response under 220 words."""


def _clean(value: Any) -> Optional[str]:
    """A displayable value, or None if there is nothing worth showing."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:      # NaN
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>", "null"}:
        return None
    return text


def _section(title: str, lines: List[str]) -> str:
    return f"{title}\n" + "\n".join(lines) if lines else ""


def _labelled(finding: Dict[str, Any], fields) -> List[str]:
    out = []
    for key, label in fields:
        value = _clean(finding.get(key))
        if value is not None:
            out.append(f"- {label}: {value}")
    return out


def _evidence_block(finding: Dict[str, Any]) -> List[str]:
    evidence = finding.get("evidence")
    if isinstance(evidence, dict) and evidence:
        return [f"- {key}: {_clean(val) if _clean(val) is not None else 'not recorded'}"
                for key, val in sorted(evidence.items())]
    if isinstance(evidence, (list, tuple)) and evidence:
        return [f"- {json.dumps(item, default=str, sort_keys=True)}" for item in evidence]
    if evidence:
        return [f"- {evidence}"]
    return ["- No structured evidence was attached to this finding."]


def _case_block(finding: Dict[str, Any]) -> List[str]:
    """
    The other findings correlated onto the same alert.

    Supervisory context the model cannot otherwise have: one alert
    carrying four separate failures is a different conversation from one
    carrying a single late acknowledgement.
    """
    siblings = finding.get("case_sibling_findings")
    if not isinstance(siblings, (list, tuple)) or not siblings:
        return []

    lines = []
    for entry in siblings:
        if isinstance(entry, dict):
            kind = _clean(entry.get("finding_type"))
            rule = _clean(entry.get("rule_id"))
            if kind:
                lines.append(f"- {kind}" + (f" ({rule})" if rule else ""))
        else:
            text = _clean(entry)
            if text:
                lines.append(f"- {text}")
    return lines


def _completeness_block(finding: Dict[str, Any]) -> List[str]:
    """
    How much of this entity's submission could be examined at all.

    Relevant because a rule that reads a record cannot fire when the
    record was never written, and the model must not read a quiet score
    as a clean one.
    """
    completeness = finding.get("evidence_completeness")
    if not isinstance(completeness, dict) or not completeness:
        return []

    lines = []
    overall = completeness.get("overall_coverage")
    if isinstance(overall, (int, float)):
        lines.append(f"- Records available for the checks that need them: "
                     f"{overall * 100:.0f}%")
    if completeness.get("limited"):
        rules = completeness.get("suppressed_rules") or []
        if rules:
            lines.append("- Evidence is materially incomplete for this entity. "
                         "These checks could only run on the portion of records "
                         "that exists: " + ", ".join(str(r) for r in rules))
        lines.append("- Treat this entity's finding counts as a floor, not a "
                     "measurement of how much went wrong.")
    return lines


def build_explanation_prompt(finding: Dict[str, Any]) -> str:
    """
    The prompt for ONE finding, built entirely from that finding.

    Deterministic: the same finding always produces the same prompt, so
    a prompt can be reproduced and audited alongside the explanation it
    generated. Absent fields are omitted rather than rendered as blanks.
    """
    finding = finding or {}
    blocks = [SYSTEM_RULES, ""]

    blocks.append("=== FINDING (decided by the deterministic rule engine) ===")
    blocks.extend(_labelled(finding, FINDING_FIELDS)
                  or ["- No identifying fields were supplied."])

    rationale = _clean(finding.get("rationale"))
    if rationale:
        blocks += ["", "=== WHY THE RULE FIRED (the engine's own rationale) ===",
                   rationale]

    blocks += ["", "=== EVIDENCE (the recorded facts behind this finding) ==="]
    blocks.extend(_evidence_block(finding))

    ranking = _labelled(finding, RANKING_FIELDS)
    if ranking:
        blocks += ["", "=== SUPERVISORY PRIORITY ==="] + ranking

    siblings = _case_block(finding)
    if siblings:
        blocks += ["", "=== OTHER FINDINGS ON THIS SAME ALERT ==="] + siblings

    entity = _labelled(finding, ENTITY_FIELDS)
    if entity:
        blocks += ["", "=== ENTITY CONTEXT ==="] + entity

    completeness = _completeness_block(finding)
    if completeness:
        blocks += ["", "=== EVIDENCE COMPLETENESS FOR THIS ENTITY ==="] + completeness

    blocks += ["", "=== YOUR TASK ===", OUTPUT_CONTRACT]
    return "\n".join(blocks)


def validate_explanation(text: Optional[str],
                          finding: Optional[Dict[str, Any]] = None) -> tuple:
    """
    Is this usable as an explanation? Returns (ok, reason).

    Deliberately shallow. It catches a model that returned nothing, was
    cut off, or echoed its instructions back. It cannot detect a
    confidently wrong statement, and pretending otherwise would be worse
    than not checking — the defence against fabrication is the prompt
    plus the fact that the examiner always has the deterministic finding
    and its evidence beside the explanation.
    """
    if text is None:
        return False, "the model returned nothing"

    cleaned = str(text).strip()
    if not cleaned:
        return False, "the model returned an empty response"
    if len(cleaned) < MIN_EXPLANATION_CHARS:
        return False, (f"the response was too short to be an explanation "
                       f"({len(cleaned)} characters)")

    lowered = cleaned.lower()
    for echo in ("you are the explanation assistant",
                 "absolute rules",
                 "=== your task ==="):
        if echo in lowered:
            return False, "the model echoed its instructions instead of answering"

    return True, ""
