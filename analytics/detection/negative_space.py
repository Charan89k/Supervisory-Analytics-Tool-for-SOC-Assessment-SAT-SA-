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


def run_all_negative_space_detectors(data: dict, alert_volume_by_soc: pd.DataFrame,
                                       alert_categories_by_soc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    frames = [
        detect_telemetry_gaps(data["telemetry"], cfg),
        detect_missing_categories(alert_categories_by_soc, cfg),
        detect_low_activity_outliers(alert_volume_by_soc, cfg),
    ]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
