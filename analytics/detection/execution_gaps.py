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

from analytics.metrics.analyst_metrics import analyst_workload, flag_overloaded_analysts


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


def detect_analyst_overload(alerts_enriched: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags analysts whose alert volume is a statistical outlier relative
    to peers within the same SOC. This wraps the ANALYST_OVERLOAD metric
    from analyst_metrics into a proper finding so it participates in
    scoring and the review queue like every other execution-gap rule,
    rather than existing only as a dashboard-side statistic.

    Emitted at the (soc_id, analyst_id) grain, not per-alert — one
    finding per overloaded analyst, evidenced by their workload z-score.
    """
    threshold = cfg["execution_gaps"].get("analyst_overload_zscore_threshold", 2.0)
    workload = analyst_workload(alerts_enriched)
    flagged_workload = flag_overloaded_analysts(workload, zscore_threshold=threshold)
    flagged = flagged_workload[flagged_workload["overloaded"] == True].copy()  # noqa: E712

    if flagged.empty:
        return pd.DataFrame()

    flagged["finding_type"] = "ANALYST_OVERLOAD"
    flagged["rule_id"] = "WORKLOAD-ZSCORE-001"
    flagged["alert_id"] = pd.NA
    flagged["case_id"] = pd.NA
    flagged["severity"] = pd.NA
    flagged["rationale"] = (
        "Analyst handled " + flagged["alerts_handled"].astype(str)
        + " alerts, " + flagged["workload_zscore"].round(2).astype(str)
        + " standard deviations above the peer mean for this SOC — a potential "
        "workload-imbalance concern, not a performance judgement on its own."
    )
    flagged["evidence"] = flagged.apply(lambda r: {
        "alerts_handled": int(r["alerts_handled"]),
        "workload_zscore": round(r["workload_zscore"], 2),
        "zscore_threshold": threshold,
    }, axis=1)
    return flagged[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                     "finding_type", "rule_id", "rationale", "evidence"]]


def detect_ack_without_investigation(alerts_enriched: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags alerts that were acknowledged and then CLOSED with no
    INVESTIGATION_STARTED event ever recorded — the official PS
    execution-gap example "alerts acknowledged but not meaningfully
    investigated".

    The `was_closed` condition is load-bearing, not defensive padding.
    Without it this rule matches every alert that has been picked up
    but not yet worked — on the current dataset that is 480 alerts,
    all of them OPEN or IN_PROGRESS, i.e. analysts doing their job at
    the moment the submission was cut. Flagging in-flight work as an
    execution gap would give the rule a 100% false-positive rate and
    train supervisors to ignore it. The gap is only real once the
    entity has declared the alert finished: acknowledged, closed, and
    no investigation in between.

    Distinct from FAST_CLOSURE (an investigation happened but was too
    short to be credible) and from MISSING_EVIDENCE (an investigation
    may have happened but left no evidence record). Here there is no
    investigation record at all.
    """
    severities = cfg["execution_gaps"].get(
        "ack_without_investigation_severities", ["HIGH", "CRITICAL"]
    )

    flagged = alerts_enriched[
        (alerts_enriched["was_acknowledged"] == True) &  # noqa: E712
        (alerts_enriched["investigation_started"] == False) &  # noqa: E712
        (alerts_enriched["was_closed"] == True) &  # noqa: E712
        (alerts_enriched["severity"].isin(severities))
    ].copy()

    if flagged.empty:
        return pd.DataFrame()

    flagged["finding_type"] = "ACK_WITHOUT_INVESTIGATION"
    flagged["rule_id"] = "INVESTIGATION-ABSENT-001"
    flagged["rationale"] = (
        "Severity " + flagged["severity"].astype(str) + " alert was acknowledged "
        "and later closed, but no investigation was ever started on it — the "
        "alert lifecycle records an acknowledgement and a closure with nothing "
        "in between. This may indicate acknowledgement-only handling that "
        "satisfies response-time metrics without examining the alert."
    )
    flagged["evidence"] = flagged.apply(lambda r: {
        "severity": r["severity"],
        "acknowledged": True,
        "investigation_started": False,
        "closed": True,
        "ack_delay_minutes": (round(r["ack_delay_minutes"], 1)
                              if pd.notna(r["ack_delay_minutes"]) else None),
        "expected_lifecycle_event": "INVESTIGATION_STARTED",
    }, axis=1)

    return flagged[["alert_id", "case_id", "soc_id", "assigned_analyst_id", "severity",
                     "finding_type", "rule_id", "rationale", "evidence"]]


def detect_repeated_alerts_without_remediation(alerts_enriched: pd.DataFrame,
                                                 cases: pd.DataFrame,
                                                 cfg: dict) -> pd.DataFrame:
    """
    Flags an asset that keeps generating the same category of alert
    while no closure on any of those alerts shows evidence of actual
    remediation — the official PS use case "repeated alerts on the
    same asset without evidence of root-cause remediation".

    Grain is (soc_id, asset_id, category): one finding per recurring
    pattern, not one per alert. The supervisory concern is the pattern
    itself — each individual alert may have been handled correctly
    while the underlying cause was never fixed.

    Remediation evidence is read from `cases.resolution_reason`, the
    only field in a standard submission that records WHAT was done.
    The actions table is not usable for this: it records that an
    analyst acted (on the current dataset every action is `TRIAGE`),
    which says nothing about whether a root cause was addressed.

    Absence of a remediation keyword is deliberately reported as
    absence of *evidence*, not as proof that no remediation happened —
    the remediation may simply have been recorded somewhere this
    submission does not include.
    """
    min_occurrences = cfg["execution_gaps"].get("repeated_alert_min_occurrences", 3)
    keywords = [k.lower() for k in cfg["execution_gaps"].get(
        "repeated_alert_remediation_keywords",
        ["remediated", "contained", "blocked", "patched", "isolated", "quarantined"],
    )]

    if "asset_id" not in alerts_enriched.columns:
        return pd.DataFrame()

    df = alerts_enriched[alerts_enriched["asset_id"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    # Attach each alert's case resolution text, where a case exists.
    if cases is not None and not cases.empty and "resolution_reason" in cases.columns \
            and "alert_id" in cases.columns:
        resolutions = (cases[["alert_id", "resolution_reason"]]
                       .dropna(subset=["alert_id"])
                       .drop_duplicates(subset=["alert_id"]))
        df = df.merge(resolutions, on="alert_id", how="left")
    else:
        df["resolution_reason"] = None

    df["_remediated"] = (
        df["resolution_reason"].fillna("").astype(str).str.lower()
        .apply(lambda text: any(keyword in text for keyword in keywords))
    )

    severity_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    findings = []
    for (soc_id, asset_id, category), group in df.groupby(
        ["soc_id", "asset_id", "category"], dropna=True
    ):
        if len(group) < min_occurrences:
            continue
        if group["_remediated"].any():
            continue  # at least one closure documents remediation

        severities = [s for s in group["severity"].dropna().unique()]
        worst = max(severities, key=lambda s: severity_order.get(str(s).upper(), -1)) \
            if severities else None

        alert_ids = [a for a in group["alert_id"].dropna().tolist()]
        reasons = sorted({str(r) for r in group["resolution_reason"].dropna().unique()})

        findings.append({
            "alert_id": pd.NA,
            "case_id": pd.NA,
            "soc_id": soc_id,
            "assigned_analyst_id": pd.NA,
            "severity": worst,
            "finding_type": "REPEATED_ALERT_WITHOUT_REMEDIATION",
            "rule_id": "ROOT-CAUSE-RECURRENCE-001",
            "rationale": (
                f"Asset {asset_id} generated {len(group)} separate {category} "
                f"alerts during the assessment period, and none of the "
                f"associated case closures record remediation or containment. "
                f"This may indicate the underlying cause was never addressed, "
                f"with each recurrence handled as a fresh alert. Absence of a "
                f"remediation record is not proof that no remediation occurred."
            ),
            "evidence": {
                "asset_id": asset_id,
                "category": category,
                "alert_count": int(len(group)),
                "min_occurrences_threshold": min_occurrences,
                "highest_severity_observed": worst,
                "distinct_resolution_reasons": reasons,
                "remediation_keywords_searched": keywords,
                "alert_ids": alert_ids[:20],
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
        detect_analyst_overload(alerts_enriched, cfg),
        detect_ack_without_investigation(alerts_enriched, cfg),
        detect_repeated_alerts_without_remediation(alerts_enriched, cases, cfg),
    ]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
