"""
Negative space detection for SAT-SA.

Negative space is evidence that SHOULD exist but is absent — missing
telemetry, missing alert categories relative to peers, or unexpectedly
low activity for an entity's size/criticality. This is fundamentally
different from execution-gap detection: there is no row to point to,
only an absence, so every finding here must explain what was expected
and why.
"""

import pandas as pd
from analytics.metrics.telemetry_metrics import expected_source_gaps


def detect_telemetry_gaps(telemetry: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    min_coverage = cfg["negative_space"]["min_expected_coverage_pct"]
    gaps = expected_source_gaps(telemetry, min_coverage)

    if gaps.empty:
        return pd.DataFrame()

    gaps = gaps.copy()
    gaps["finding_type"] = "TELEMETRY_GAP"
    gaps["rule_id"] = "TELEMETRY-COVERAGE-001"
    gaps["rationale"] = (
        "Expected telemetry source '" + gaps["source_name"] + "' shows "
        + gaps["coverage_percentage"].round(1).astype(str)
        + "% coverage, below the required " + str(min_coverage) + "% threshold "
        "(status: " + gaps["health_status"] + ") — a potential monitoring "
        "blind spot for this entity."
    )
    gaps["evidence"] = gaps.apply(lambda r: {
        "source_name": r["source_name"],
        "coverage_percentage": r["coverage_percentage"],
        "required_coverage_percentage": min_coverage,
        "health_status": r["health_status"],
    }, axis=1)
    return gaps[["soc_id", "source_name", "finding_type", "rule_id", "rationale", "evidence"]]


def detect_missing_categories(alert_categories_by_soc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    alert_categories_by_soc: output of alert_metrics.category_coverage_by_soc
    (soc_id -> set of categories observed).

    Flags an entity as missing a category if that category is present
    for a strong majority of peer entities but absent for this one.
    """
    presence_fraction = cfg["negative_space"]["missing_category_peer_presence_fraction"]

    all_categories = set()
    for cats in alert_categories_by_soc["categories_observed"]:
        all_categories |= cats

    n_socs = len(alert_categories_by_soc)
    findings = []
    for category in all_categories:
        present_mask = alert_categories_by_soc["categories_observed"].apply(lambda s: category in s)
        peer_presence = present_mask.sum() / n_socs
        if peer_presence < presence_fraction:
            continue  # not common enough across peers to be "expected"

        for _, row in alert_categories_by_soc[~present_mask].iterrows():
            findings.append({
                "soc_id": row["soc_id"],
                "category": category,
                "finding_type": "MISSING_ALERT_CATEGORY",
                "rule_id": "CATEGORY-PEER-COVERAGE-001",
                "rationale": (
                    f"'{category}' alerts appear for "
                    f"{round(peer_presence * 100, 1)}% of peer entities but "
                    f"were not observed for this entity during the assessment period."
                ),
            })
    return pd.DataFrame(findings)


def detect_low_activity_outliers(alert_volume_by_soc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags entities whose total alert volume is a statistical low
    outlier relative to peers — a proxy for monitoring blind spots
    or under-instrumented environments.
    """
    threshold = cfg["negative_space"]["low_activity_zscore_threshold"]
    df = alert_volume_by_soc.copy()

    mean, std = df["alert_count"].mean(), df["alert_count"].std()
    if not std or pd.isna(std):
        return pd.DataFrame()

    df["volume_zscore"] = (df["alert_count"] - mean) / std
    flagged = df[df["volume_zscore"] <= threshold].copy()

    if flagged.empty:
        return pd.DataFrame()

    flagged["finding_type"] = "LOW_ACTIVITY_OUTLIER"
    flagged["rule_id"] = "VOLUME-ZSCORE-001"
    flagged["rationale"] = (
        "Alert volume (" + flagged["alert_count"].astype(str) + ") is "
        + flagged["volume_zscore"].round(2).astype(str)
        + " standard deviations below the peer mean, suggesting a possible "
        "monitoring gap rather than genuinely low risk."
    )
    return flagged[["soc_id", "alert_count", "finding_type", "rule_id", "rationale"]]


def detect_missing_escalation_records(alerts_enriched: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags entities where HIGH/CRITICAL alerts exist but NO escalation
    record of any kind (required or not) is present for the whole SOC.
    This is deliberately distinct from MISSED_ESCALATION (an execution
    gap, where a specific alert's escalation record shows it should
    have escalated but didn't): this rule catches the absence case —
    a SOC that doesn't appear to keep escalation records at all, which
    a per-alert rule can never surface because there's no row to flag.
    """
    required_severities = cfg["escalation"]["required_severities"]
    high_crit = alerts_enriched[alerts_enriched["severity"].isin(required_severities)]
    if high_crit.empty:
        return pd.DataFrame()

    has_any_escalation_data = alerts_enriched["escalation_required"].notna()
    socs_with_records = set(alerts_enriched.loc[has_any_escalation_data, "soc_id"].unique())
    socs_with_high_crit = set(high_crit["soc_id"].unique())
    missing_socs = socs_with_high_crit - socs_with_records

    if not missing_socs:
        return pd.DataFrame()

    findings = []
    for soc_id in missing_socs:
        count = len(high_crit[high_crit["soc_id"] == soc_id])
        findings.append({
            "soc_id": soc_id,
            "finding_type": "MISSING_ESCALATION_RECORDS",
            "rule_id": "ESCALATION-RECORDKEEPING-001",
            "rationale": (
                f"{count} HIGH/CRITICAL alert(s) exist for this entity but no "
                "escalation record of any kind was found for the assessment "
                "period — a potential gap in escalation record-keeping, "
                "distinct from any single alert failing to escalate."
            ),
            "evidence": {"high_critical_alert_count": count, "escalation_records_found": 0},
        })
    return pd.DataFrame(findings)


def detect_missing_investigations(cases: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Flags entities where a strong majority of cases carry no
    investigation notes at all (as opposed to REPETITIVE_INVESTIGATION,
    which fires on duplicate-but-present notes). Absence of any note
    text is a negative-space signal — there's no record to point a
    per-case rule at.
    """
    if cases.empty or "investigation_notes" not in cases.columns:
        return pd.DataFrame()

    min_case_count = cfg["negative_space"].get("missing_investigation_min_cases", 5)
    empty_fraction_threshold = cfg["negative_space"].get("missing_investigation_empty_fraction", 0.5)

    findings = []
    for soc_id, group in cases.groupby("soc_id"):
        if len(group) < min_case_count:
            continue
        notes = group["investigation_notes"].fillna("").astype(str).str.strip()
        empty_fraction = (notes == "").mean()
        if empty_fraction < empty_fraction_threshold:
            continue
        findings.append({
            "soc_id": soc_id,
            "finding_type": "MISSING_INVESTIGATIONS",
            "rule_id": "INVESTIGATION-RECORDKEEPING-001",
            "rationale": (
                f"{round(empty_fraction * 100, 1)}% of cases for this entity "
                f"have no investigation notes at all, above the "
                f"{round(empty_fraction_threshold * 100, 1)}% threshold — a "
                "potential investigation record-keeping gap."
            ),
            "evidence": {
                "case_count": int(len(group)),
                "empty_investigation_fraction": round(empty_fraction, 3),
                "threshold_fraction": empty_fraction_threshold,
            },
        })
    return pd.DataFrame(findings)


def run_all_negative_space_detectors(data: dict, alert_volume_by_soc: pd.DataFrame,
                                       alert_categories_by_soc: pd.DataFrame, cfg: dict,
                                       alerts_enriched: pd.DataFrame = None) -> pd.DataFrame:
    frames = [
        detect_telemetry_gaps(data["telemetry"], cfg),
        detect_missing_categories(alert_categories_by_soc, cfg),
        detect_low_activity_outliers(alert_volume_by_soc, cfg),
        detect_missing_investigations(data.get("cases", pd.DataFrame()), cfg),
    ]
    if alerts_enriched is not None:
        frames.append(detect_missing_escalation_records(alerts_enriched, cfg))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
