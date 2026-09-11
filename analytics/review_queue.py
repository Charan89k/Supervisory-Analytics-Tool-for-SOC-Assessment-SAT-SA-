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
    review_cfg = cfg.get("review_queue", {})
    boosts = review_cfg.get("severity_boost", {})
    top_n = review_cfg.get("top_n", 25)
    per_entity_cases = int(review_cfg.get("per_entity_cases", 1))

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
            "case_priority", "case_finding_count", "selection_reason",
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

    # Rank cases by case_priority, then sort so all findings in the same
    # case sit next to each other with the worst case first, and within
    # a case the worst finding first.
    #
    # case_id_key breaks ties. Without it the sort is over one column,
    # which pandas performs with an unstable algorithm, so two cases of
    # equal priority could swap places between runs or pandas versions.
    # Priorities here are small discrete numbers and ties are common, so
    # that is a real reproducibility risk for a tool whose output is
    # meant to be re-derivable by an examiner.
    case_order = (
        all_findings.groupby("case_id_key", sort=False)
        .agg(case_priority=("queue_priority", "sum"),
             soc_id=("soc_id", "first"))
        .reset_index()
        .sort_values(["case_priority", "case_id_key"], ascending=[False, True])
        .reset_index(drop=True)
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

    # ---- selection: global priority, plus per-entity coverage --------
    #
    # A purely global top-N starves entities. Measured on a 30-entity
    # submission: 21 of 30 received no queue item at all, including one
    # ranked 3rd by risk score with 202 CRITICAL findings. A few
    # high-volume entities own every top case, and a supervisor working
    # the queue would never look at the other 70% of the CSEs they are
    # responsible for.
    #
    # So selection happens at two levels. The global top-N is unchanged.
    # On top of it, every entity contributes its own worst case(s), so
    # each CSE with anything worth reviewing is represented. Ordering is
    # untouched — still case_priority, descending — and every selected
    # case keeps the global case_rank it earned, so nothing is promoted
    # above a case that outranks it.
    #
    # An entity with no findings produces no cases and is not forced in:
    # a clean entity is absent from the queue, which is the correct
    # supervisory statement about it.
    global_keys = set(case_order.head(top_n)["case_id_key"])

    entity_keys = set()
    if per_entity_cases > 0:
        entity_keys = set(
            case_order.sort_values(
                ["soc_id", "case_priority", "case_id_key"],
                ascending=[True, False, True])
            .groupby("soc_id", sort=False)
            .head(per_entity_cases)["case_id_key"]
        )

    selected = global_keys | entity_keys

    # Keep every finding belonging to a selected case, not just the
    # first N rows — a 3-finding case ranked #1 should show all 3
    # findings, even if that means more rows than there are cases.
    queue = queue[queue["case_id_key"].isin(selected)].reset_index(drop=True)

    # Why each case is in the queue, so the UI can separate "the worst
    # things anywhere" from "the worst thing at this CSE" without
    # re-deriving the selection.
    queue["selection_reason"] = queue["case_id_key"].apply(
        lambda key: "global_priority" if key in global_keys else "entity_coverage"
    )

    keep_cols = [c for c in [
        "queue_rank", "case_rank", "soc_id", "finding_type", "rule_id", "severity", "alert_id", "case_id",
        "assigned_analyst_id", "rationale", "evidence", "finding_weight",
        "severity_boost", "queue_priority", "case_priority", "case_finding_count",
        "selection_reason",
    ] if c in queue.columns]

    return queue[keep_cols]
