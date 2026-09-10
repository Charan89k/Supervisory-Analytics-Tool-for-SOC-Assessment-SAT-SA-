"""
Composite entity risk scoring for SAT-SA.

Deliberately simple and linear: score = sum(weight_i * normalized_count_i)
so a supervisor can recompute any entity's score by hand from the
finding counts alone. No black-box model sits between the findings
and the final ranking — that traceability is a scored requirement.
"""

import pandas as pd


def build_finding_counts(execution_gap_findings: pd.DataFrame,
                          negative_space_findings: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if not execution_gap_findings.empty:
        frames.append(execution_gap_findings[["soc_id", "finding_type"]])
    if not negative_space_findings.empty:
        frames.append(negative_space_findings[["soc_id", "finding_type"]])

    if not frames:
        return pd.DataFrame(columns=["soc_id"])

    all_findings = pd.concat(frames, ignore_index=True)
    counts = (all_findings.groupby(["soc_id", "finding_type"])
              .size().unstack(fill_value=0))
    counts.columns = [c.lower() for c in counts.columns]
    return counts.reset_index()


def compute_entity_risk_scores(finding_counts: pd.DataFrame, alert_volume_by_soc: pd.DataFrame,
                                 cfg: dict) -> pd.DataFrame:
    weights = cfg["scoring"]["weights"]
    normalize_per = cfg["scoring"]["normalize_per"]

    df = finding_counts.merge(alert_volume_by_soc[["soc_id", "alert_count"]], on="soc_id", how="left")
    df["alert_count"] = df["alert_count"].fillna(1).clip(lower=1)

    score_components = []
    for finding_type, weight in weights.items():
        col = finding_type
        if col not in df.columns:
            df[col] = 0
        normalized_col = f"{col}_normalized"
        df[normalized_col] = (df[col] / df["alert_count"]) * normalize_per
        df[f"{col}_weighted"] = df[normalized_col] * weight
        score_components.append(f"{col}_weighted")

    df["supervisory_risk_score"] = df[score_components].sum(axis=1)
    df = df.sort_values("supervisory_risk_score", ascending=False).reset_index(drop=True)
    df["priority_rank"] = df.index + 1
    return df


def score_breakdown_for_entity(risk_scores: pd.DataFrame, soc_id: str, cfg: dict) -> dict:
    """
    Returns the exact arithmetic behind one entity's score, for the
    explainability/drill-down view — every number here should be
    independently verifiable against the raw finding counts.
    """
    row = risk_scores[risk_scores["soc_id"] == soc_id]
    if row.empty:
        return {}
    row = row.iloc[0]
    weights = cfg["scoring"]["weights"]

    breakdown = {"soc_id": soc_id, "total_score": round(row["supervisory_risk_score"], 2), "components": []}
    for finding_type, weight in weights.items():
        raw_count = row.get(finding_type, 0)
        normalized = row.get(f"{finding_type}_normalized", 0)
        contribution = row.get(f"{finding_type}_weighted", 0)
        breakdown["components"].append({
            "finding_type": finding_type,
            "raw_count": int(raw_count),
            "normalized_per_100": round(float(normalized), 3),
            "weight": weight,
            "contribution": round(float(contribution), 3),
        })
    return breakdown
