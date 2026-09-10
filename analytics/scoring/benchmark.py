"""Peer benchmarking for SAT-SA — ranks entities against comparable peers."""

import pandas as pd


def add_peer_group(socs: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = socs.copy()
    group_by = cfg["benchmarking"]["peer_group_by"]
    if group_by == "sector":
        df["peer_group"] = df["sector"]
    else:
        df["peer_group"] = "all"
    return df


def percentile_rank_by_peer_group(metric_df: pd.DataFrame, socs_with_group: pd.DataFrame,
                                    metric_column: str) -> pd.DataFrame:
    """
    Returns metric_df with an added `<metric_column>_percentile` column:
    where this entity ranks (0-100) among its peer group on this metric.
    Higher percentile = higher value of the metric, not necessarily "better" —
    interpretation depends on the metric (e.g., high compliance_rate is good,
    high missed-escalation count is bad).
    """
    merged = metric_df.merge(socs_with_group[["soc_id", "peer_group"]], on="soc_id", how="left")
    merged[f"{metric_column}_percentile"] = (
        merged.groupby("peer_group")[metric_column].rank(pct=True) * 100
    )
    return merged
