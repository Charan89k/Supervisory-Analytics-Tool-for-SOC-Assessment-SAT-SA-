"""
What each rule checks, and which configured thresholds it used.

Completes the Finding -> RULE link. A rule_id alone is a token; an
examiner asking "what does ESC-REQUIRED-001 actually test, and against
what number?" should not have to read the source.

Every threshold shown is read from the live assessment configuration,
so the page cannot drift from the values the run actually used. Nothing
here restates a finding or adds judgement — it describes the rule.
"""

from __future__ import annotations

from typing import Dict, List

#: The two supervisory categories a rule belongs to. Held here because
#: this is already the authoritative rule catalogue, and because a
#: review-queue record does not carry the category the findings list
#: attaches — deriving it from the rule keeps both views consistent.
EXECUTION_GAP = "Execution Gap"
NEGATIVE_SPACE = "Negative Space"

#: rule_id -> (finding_type, category, what it tests, config keys it reads)
RULES: Dict[str, dict] = {
    "ESC-REQUIRED-001": {
        "category": EXECUTION_GAP,
        "finding_type": "MISSED_ESCALATION",
        "checks": (
            "An alert whose escalation record says escalation was "
            "required, but also that none was initiated."
        ),
        "config": ["escalation.required_severities"],
    },
    "ACK-SLA-001": {
        "category": EXECUTION_GAP,
        "finding_type": "SLOW_TRIAGE",
        "checks": (
            "Time from alert creation to acknowledgement, against a "
            "multiple of the acknowledgement SLA for that severity."
        ),
        "config": ["sla_minutes.ack",
                    "execution_gaps.slow_triage_sla_multiplier"],
    },
    "TRIAGE-FAST-001": {
        "category": EXECUTION_GAP,
        "finding_type": "FAST_CLOSURE",
        "checks": (
            "Investigation duration on a HIGH/CRITICAL alert against a "
            "fraction of the triage SLA, with a minimum floor and a "
            "margin so borderline cases do not fire."
        ),
        "config": ["sla_minutes.triage",
                    "execution_gaps.fast_closure_sla_fraction",
                    "execution_gaps.fast_closure_floor_minutes",
                    "execution_gaps.fast_closure_min_margin_minutes"],
    },
    "EVIDENCE-REQUIRED-001": {
        "category": EXECUTION_GAP,
        "finding_type": "MISSING_EVIDENCE",
        "checks": (
            "A HIGH/CRITICAL alert closed with no evidence record "
            "attached. Indicates absence of a record, not that no "
            "investigation occurred."
        ),
        "config": [],
    },
    "REOPEN-001": {
        "category": EXECUTION_GAP,
        "finding_type": "REOPENED_CASE",
        "checks": "A REOPENED event present in the alert lifecycle trail.",
        "config": [],
    },
    "TEMPLATE-INVESTIGATION-001": {
        "category": EXECUTION_GAP,
        "finding_type": "REPETITIVE_INVESTIGATION",
        "checks": (
            "Byte-identical investigation note text repeated across "
            "cases by the same analyst. Exact match, not fuzzy "
            "similarity, which would flag genuine notes sharing a "
            "template."
        ),
        "config": ["execution_gaps.repetitive_investigation_min_occurrences"],
    },
    "WORKLOAD-ZSCORE-001": {
        "category": EXECUTION_GAP,
        "finding_type": "ANALYST_OVERLOAD",
        "checks": (
            "An analyst's alert volume as a z-score against peers in "
            "the same SOC."
        ),
        "config": ["execution_gaps.analyst_overload_zscore_threshold"],
    },
    "INVESTIGATION-ABSENT-001": {
        "category": EXECUTION_GAP,
        "finding_type": "ACK_WITHOUT_INVESTIGATION",
        "checks": (
            "An alert acknowledged and later closed with no "
            "INVESTIGATION_STARTED event ever recorded. Closure is "
            "required so alerts still being worked are not flagged."
        ),
        "config": ["execution_gaps.ack_without_investigation_severities"],
    },
    "ROOT-CAUSE-RECURRENCE-001": {
        "category": EXECUTION_GAP,
        "finding_type": "REPEATED_ALERT_WITHOUT_REMEDIATION",
        "checks": (
            "Repeated alerts of one category on one asset where no case "
            "closure records remediation or containment. Absence of a "
            "remediation record, not proof none occurred."
        ),
        "config": ["execution_gaps.repeated_alert_min_occurrences",
                    "execution_gaps.repeated_alert_remediation_keywords"],
    },
    "TELEMETRY-COVERAGE-001": {
        "category": NEGATIVE_SPACE,
        "finding_type": "TELEMETRY_GAP",
        "checks": (
            "An expected telemetry source whose reported coverage is "
            "below the required threshold."
        ),
        "config": ["negative_space.min_expected_coverage_pct"],
    },
    "CATEGORY-PEER-COVERAGE-001": {
        "category": NEGATIVE_SPACE,
        "finding_type": "MISSING_ALERT_CATEGORY",
        "checks": (
            "An alert category present for a strong majority of peer "
            "entities but not observed for this one."
        ),
        "config": ["negative_space.missing_category_peer_presence_fraction"],
    },
    "VOLUME-ZSCORE-001": {
        "category": NEGATIVE_SPACE,
        "finding_type": "LOW_ACTIVITY_OUTLIER",
        "checks": (
            "Entity alert volume as a z-score below the peer mean."
        ),
        "config": ["negative_space.low_activity_zscore_threshold"],
    },
    "ESCALATION-RECORDKEEPING-001": {
        "category": NEGATIVE_SPACE,
        "finding_type": "MISSING_ESCALATION_RECORDS",
        "checks": (
            "The fraction of an entity's HIGH/CRITICAL alerts carrying "
            "an escalation record of any kind. Measures coverage, not "
            "total absence."
        ),
        "config": ["negative_space.escalation_record_coverage_fraction",
                    "negative_space.escalation_record_min_high_crit_alerts"],
    },
    "INVESTIGATION-RECORDKEEPING-001": {
        "category": NEGATIVE_SPACE,
        "finding_type": "MISSING_INVESTIGATIONS",
        "checks": (
            "The fraction of an entity's cases with no investigation "
            "notes at all."
        ),
        "config": ["negative_space.missing_investigation_min_cases",
                    "negative_space.missing_investigation_empty_fraction"],
    },
}


def _lookup(config: dict, dotted: str):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def describe(rule_id: str, config: dict) -> dict:
    """
    What this rule tests, and the threshold values the run used.

    Returns {} for an unknown rule rather than inventing a description —
    a rule the reference does not know about should show its evidence
    and nothing more.
    """
    entry = RULES.get(str(rule_id))
    if entry is None:
        return {}

    thresholds: List[tuple] = []
    for key in entry["config"]:
        value = _lookup(config, key)
        if value is not None:
            thresholds.append((key, value))

    return {
        "finding_type": entry["finding_type"],
        "category": entry["category"],
        "checks": entry["checks"],
        "thresholds": thresholds,
    }


def category_for(rule_id: str) -> str:
    """
    Which supervisory category a rule belongs to.

    Returns "" for an unknown rule rather than guessing — the detail
    panel omits the row instead of printing a category it cannot
    substantiate.
    """
    entry = RULES.get(str(rule_id))
    return entry["category"] if entry else ""
