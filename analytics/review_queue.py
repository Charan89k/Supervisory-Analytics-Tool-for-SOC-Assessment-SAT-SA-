"""
Supervisory Review Queue for SAT-SA.

The risk score ranks *entities*. This module ranks individual
*findings* across all entities into a single prioritized queue —
the highest-value things for a human reviewer to look at first,
regardless of which entity they belong to.

Ranking is deliberately transparent and linear, same philosophy as
scoring/score.py: queue_priority = finding_weight * severity_boost.
No black-box model decides review order — a supervisor can see
exactly why item #1 outranks item #2.

This module NEVER decides whether something is a finding — that
already happened in the detection layer. It only orders findings
that already exist.
"""

import pandas as pd


def _severity_boost(severity, boosts: dict) -> float:
    if severity is None or (isinstance(severity, float) and pd.isna(severity)):
        return boosts.get("MEDIUM", 1.0)
    return boosts.get(str(severity).upper(), boosts.get("MEDIUM", 1.0))


def build_review_queue(execution_gap_findings: pd.DataFrame,
                        negative_space_findings: pd.DataFrame,
                        cfg: dict) -> pd.DataFrame:
    """
    Combines execution-gap and negative-space findings into one
    ranked queue, correlating multiple findings on the same alert
    into a single "case" so a supervisor sees compounding problems
    together instead of as unrelated rows.

    Correlation key is (soc_id, alert_id) for execution-gap findings,
    which carry an alert_id. Negative-space findings are entity/source
    -level (e.g. TELEMETRY_GAP has no alert_id) and are never merged
    with each other or with alert-level findings — each stays its own
    single-finding case.

    Returns one row per finding (same shape as before), sorted so that
    all findings belonging to the same case are adjacent, ranked by
    case_priority (sum of that case's finding-level queue_priority
    values — deliberately additive, same "more wrong things = worse"
    philosophy as entity scoring, and still hand-verifiable). Adds:
        case_id_key       internal correlation key, not for display
        case_priority      sum of queue_priority across the case
        case_finding_count number of findings correlated into this case
        case_rank          dense rank of the case by case_priority (1 = worst)
    Columns: soc_id, finding_type, rule_id, severity, alert_id,
    case_id, assigned_analyst_id, rationale, evidence, finding_weight,
    severity_boost, queue_priority, case_priority, case_finding_count,
    case_rank, queue_rank.
    """
    weights = cfg["scoring"]["weights"]
    boosts = cfg.get("review_queue", {}).get("severity_boost", {})
    top_n = cfg.get("review_queue", {}).get("top_n", 25)

    frames = []
    if execution_gap_findings is not None and not execution_gap_findings.empty:
        frames.append(execution_gap_findings.copy())
    if negative_space_findings is not None and not negative_space_findings.empty:
        frames.append(negative_space_findings.copy())

    if not frames:
        return pd.DataFrame(columns=[
            "queue_rank", "case_rank", "soc_id", "finding_type", "rule_id", "severity",
            "alert_id", "case_id", "assigned_analyst_id", "rationale", "evidence",
            "finding_weight", "severity_boost", "queue_priority",
            "case_priority", "case_finding_count",
        ])

    all_findings = pd.concat(frames, ignore_index=True)

    all_findings["finding_weight"] = (
        all_findings["finding_type"].str.lower().map(weights).fillna(1.0)
    )
    severity_col = all_findings["severity"] if "severity" in all_findings.columns else pd.Series(
        [None] * len(all_findings)
    )
    all_findings["severity_boost"] = severity_col.apply(lambda s: _severity_boost(s, boosts))
    all_findings["queue_priority"] = all_findings["finding_weight"] * all_findings["severity_boost"]

    # Correlation key: (soc_id, alert_id) when alert_id exists and is
    # non-null (execution-gap findings); otherwise each row is its own
    # case, keyed by its own position so it never merges with anything.
    if "alert_id" not in all_findings.columns:
        all_findings["alert_id"] = pd.NA

    all_findings["case_id_key"] = all_findings.apply(
        lambda r: f"soc::{r['soc_id']}::alert::{r['alert_id']}"
        if pd.notna(r["alert_id"]) else f"single::{r.name}",
        axis=1,
    )

    case_priority = all_findings.groupby("case_id_key")["queue_priority"].transform("sum")
    case_finding_count = all_findings.groupby("case_id_key")["queue_priority"].transform("count")
    all_findings["case_priority"] = case_priority
    all_findings["case_finding_count"] = case_finding_count

    # Dense-rank cases by case_priority (ties share a rank), then sort
    # so all findings in the same case sit next to each other with the
    # worst case first, and within a case the worst finding first.
    case_order = (
        all_findings[["case_id_key", "case_priority"]]
        .drop_duplicates()
        .sort_values("case_priority", ascending=False)
    )
    case_order["case_rank"] = range(1, len(case_order) + 1)

    all_findings = all_findings.merge(
        case_order[["case_id_key", "case_rank"]], on="case_id_key", how="left"
    )

    queue = all_findings.sort_values(
        ["case_priority", "case_id_key", "queue_priority"],
        ascending=[False, True, False],
    ).reset_index(drop=True)
    queue["queue_rank"] = queue.index + 1

    # Keep every finding belonging to a top-N case, not just the first
    # N rows — a 3-finding case ranked #1 should show all 3 findings,
    # even if that means slightly more than top_n total rows.
    top_case_keys = case_order.head(top_n)["case_id_key"]
    queue = queue[queue["case_id_key"].isin(top_case_keys)].reset_index(drop=True)

    keep_cols = [c for c in [
        "queue_rank", "case_rank", "soc_id", "finding_type", "rule_id", "severity", "alert_id", "case_id",
        "assigned_analyst_id", "rationale", "evidence", "finding_weight",
        "severity_boost", "queue_priority", "case_priority", "case_finding_count",
    ] if c in queue.columns]

    return queue[keep_cols]
