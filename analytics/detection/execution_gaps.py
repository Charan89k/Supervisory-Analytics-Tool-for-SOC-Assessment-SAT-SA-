"""
Execution gap detection for SAT-SA.

An execution gap is: documented controls/policy say one thing should
happen, but the operational evidence shows it didn't.

Architecture: every function returns findings with THREE distinct
layers, kept separate on purpose:

    evidence     -- raw, structured facts (numbers, thresholds).
                    Never phrased as a judgement.
    finding_type -- the deterministic rule outcome (e.g. SLOW_TRIAGE).
                    This is what the rule engine decided, objectively,
                    from the evidence.
    rationale    -- a human-readable INDICATOR, not an accusation.
                    Phrased as "may indicate" / "suggests a potential
                    concern", never "the analyst failed" or similar.
                    Supervisory judgement belongs to the human
                    reviewer, not this layer.

This separation is what lets the eventual LLM narrate findings
without ever being the thing that decides whether a gap occurred.
"""

import pandas as pd


def detect_missed_escalations(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    df = alerts_enriched[
        (alerts_enriched["escalation_required"] == True) &  # noqa: E712
        (alerts_enriched["escalation_initiated"] == False)  # noqa: E712
    ].copy()

    df["finding_type"] = "MISSED_ESCALATION"
    df["rule_id"] = "ESC-REQUIRED-001"
    df["rationale"] = (
        "Severity " + df["severity"] + " alert required escalation per policy "
        "but no escalation record shows one was initiated — a potential "
        "escalation-compliance concern."
    )
    df["evidence"] = df.apply(lambda r: {
        "severity": r["severity"],
        "escalation_required": True,
        "escalation_initiated": False,
    }, axis=1)
    return df[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                "finding_type", "rule_id", "rationale", "evidence"]]


def detect_fast_closures(alerts_enriched: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    sla_triage = cfg["sla_minutes"]["triage"]
    fraction = cfg["execution_gaps"]["fast_closure_sla_fraction"]
    floor_minutes = cfg["execution_gaps"]["fast_closure_floor_minutes"]
    min_margin = cfg["execution_gaps"]["fast_closure_min_margin_minutes"]

    high_crit = alerts_enriched[alerts_enriched["severity"].isin(["HIGH", "CRITICAL"])].copy()

    def threshold_for(row):
        return max(floor_minutes, sla_triage.get(row["severity"], 90) * fraction)

    high_crit["_threshold"] = high_crit.apply(threshold_for, axis=1)
    high_crit["_deviation"] = high_crit["_threshold"] - high_crit["investigation_duration_minutes"]

    flagged = high_crit[
        high_crit["investigation_duration_minutes"].notna() &
        (high_crit["_deviation"] >= min_margin)
    ].copy()

    flagged["finding_type"] = "FAST_CLOSURE"
    flagged["rule_id"] = "TRIAGE-FAST-001"
    flagged["rationale"] = (
        "Severity " + flagged["severity"] + " alert was investigated for only "
        + flagged["investigation_duration_minutes"].round(1).astype(str)
        + " minutes, " + flagged["_deviation"].round(1).astype(str)
        + " minutes below the expected minimum — a potential premature-closure concern."
    )
    flagged["evidence"] = flagged.apply(lambda r: {
        "severity": r["severity"],
        "investigation_duration_minutes": round(r["investigation_duration_minutes"], 1),
        "expected_minimum_minutes": round(r["_threshold"], 1),
        "deviation_minutes": round(r["_deviation"], 1),
        "min_margin_required_minutes": min_margin,
    }, axis=1)
    return flagged[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                     "finding_type", "rule_id", "rationale", "evidence"]]


def detect_slow_triage(alerts_enriched: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    sla_ack = cfg["sla_minutes"]["ack"]
    multiplier = cfg["execution_gaps"]["slow_triage_sla_multiplier"]

    df = alerts_enriched.copy()
    df["_sla_target"] = df["severity"].map(sla_ack)
    df["_threshold"] = df["_sla_target"] * multiplier
    flagged = df[
        df["ack_delay_minutes"].notna() &
        (df["ack_delay_minutes"] > df["_threshold"])
    ].copy()

    flagged["finding_type"] = "SLOW_TRIAGE"
    flagged["rule_id"] = "ACK-SLA-001"
    flagged["rationale"] = (
        "Acknowledgement took " + flagged["ack_delay_minutes"].round(1).astype(str)
        + " minutes, exceeding " + str(multiplier) + "x the "
        + flagged["_sla_target"].astype(str) + "-minute SLA target for "
        + flagged["severity"] + " severity — a potential triage-delay concern."
    )
    flagged["evidence"] = flagged.apply(lambda r: {
        "severity": r["severity"],
        "ack_delay_minutes": round(r["ack_delay_minutes"], 1),
        "sla_target_minutes": r["_sla_target"],
        "threshold_multiplier": multiplier,
        "threshold_minutes": round(r["_threshold"], 1),
    }, axis=1)
    return flagged[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                     "finding_type", "rule_id", "rationale", "evidence"]]


def detect_missing_evidence(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    flagged = alerts_enriched[
        (alerts_enriched["has_evidence"] == False) &  # noqa: E712
        (alerts_enriched["severity"].isin(["HIGH", "CRITICAL"]))
    ].copy()

    flagged["finding_type"] = "MISSING_EVIDENCE"
    flagged["rule_id"] = "EVIDENCE-REQUIRED-001"
    flagged["rationale"] = (
        "Severity " + flagged["severity"] + " alert was closed with no evidence "
        "record attached — a potential documentation-quality concern. This "
        "indicates absence of an evidence record, not that no investigation occurred."
    )
    flagged["evidence"] = flagged.apply(lambda r: {
        "severity": r["severity"],
        "evidence_record_count": 0,
    }, axis=1)
    return flagged[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                     "finding_type", "rule_id", "rationale", "evidence"]]


def detect_reopened_cases(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    flagged = alerts_enriched[alerts_enriched["was_reopened"] == True].copy()  # noqa: E712
    flagged["finding_type"] = "REOPENED_CASE"
    flagged["rule_id"] = "REOPEN-001"
    flagged["rationale"] = (
        "Case was closed and subsequently reopened, which may indicate the "
        "initial resolution was incomplete or premature. Reopening can also "
        "reflect legitimate new information and is not itself proof of error."
    )
    flagged["evidence"] = flagged.apply(lambda r: {"was_reopened": True}, axis=1)
    return flagged[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                     "finding_type", "rule_id", "rationale", "evidence"]]


def detect_repetitive_investigations(cases: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags EXACT-text-duplicate investigation notes repeated across many
    cases assigned to the same analyst — an INDICATOR of template-driven,
    copy-paste investigation, not a determination of misconduct.

    Uses exact string equality rather than fuzzy similarity. Fuzzy
    similarity (e.g. difflib.SequenceMatcher) is the wrong tool here:
    two genuine, alert-specific notes sharing a common phrasing template
    but differing only in an embedded asset ID / IP / technique will
    still score >0.9 similarity purely because the shared boilerplate
    dominates the ratio, producing a large false-positive rate. Genuine
    investigation notes should always contain some alert-specific
    identifier and therefore almost never be byte-for-byte identical;
    exact duplication is the actual signal worth flagging.

    IMPORTANT: this must run against `investigation_notes` (genuine
    free text), never `resolution_reason` (a coarse, deliberately
    templated field with only a few dozen possible values dataset-wide).
    """
    min_occurrences = cfg["execution_gaps"]["repetitive_investigation_min_occurrences"]
    note_column = "investigation_notes" if "investigation_notes" in cases.columns else "resolution_reason"

    findings = []
    for (soc_id, analyst_id), group in cases.groupby(["soc_id", "assigned_analyst_id"]):
        notes = group[note_column].fillna("")
        dupe_counts = notes.value_counts()
        repeated_notes = dupe_counts[dupe_counts >= min_occurrences]

        for note_text, occurrence_count in repeated_notes.items():
            if not note_text:
                continue
            matching_rows = group[notes == note_text]
            for _, row in matching_rows.iterrows():
                findings.append({
                    "alert_id": row.get("alert_id"),
                    "case_id": row["case_id"],
                    "soc_id": soc_id,
                    "assigned_analyst_id": analyst_id,
                    "severity": row["severity"],
                    "finding_type": "REPETITIVE_INVESTIGATION",
                    "rule_id": "TEMPLATE-INVESTIGATION-001",
                    "rationale": (
                        f"This analyst used the exact same investigation note text "
                        f"across {occurrence_count} separate cases, which may indicate "
                        f"template-driven rather than case-specific investigation. "
                        f"This is an indicator for supervisory review, not a finding "
                        f"of misconduct on its own."
                    ),
                    "evidence": {
                        "identical_note_count": int(occurrence_count),
                        "min_occurrences_threshold": min_occurrences,
                    },
                })
    return pd.DataFrame(findings)


def run_all_execution_gap_detectors(alerts_enriched: pd.DataFrame, cases: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    frames = [
        detect_missed_escalations(alerts_enriched),
        detect_fast_closures(alerts_enriched, cfg),
        detect_slow_triage(alerts_enriched, cfg),
        detect_missing_evidence(alerts_enriched),
        detect_reopened_cases(alerts_enriched),
        detect_repetitive_investigations(cases, cfg),
    ]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
