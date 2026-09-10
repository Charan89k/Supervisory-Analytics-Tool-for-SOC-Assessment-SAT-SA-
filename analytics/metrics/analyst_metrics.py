"""Analyst-level descriptive metrics — feeds ANALYST_OVERLOAD-style checks."""

import pandas as pd


def analyst_workload(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    workload = (alerts_enriched.groupby(["soc_id", "assigned_analyst_id"])
                .agg(alerts_handled=("alert_id", "count"),
                     avg_ack_delay_minutes=("ack_delay_minutes", "mean"),
                     avg_investigation_minutes=("investigation_duration_minutes", "mean"))
                .reset_index())
    return workload


def flag_overloaded_analysts(workload: pd.DataFrame, zscore_threshold: float = 2.0) -> pd.DataFrame:
    """
    Flags analysts whose alert volume is a statistical outlier relative
    to peers within the same SOC — a workload-imbalance indicator, not
    a performance judgement on its own.
    """
    flagged = workload.copy()
    group_mean = flagged.groupby("soc_id")["alerts_handled"].transform("mean")
    group_std = flagged.groupby("soc_id")["alerts_handled"].transform("std").fillna(0)

    flagged["workload_zscore"] = 0.0
    nonzero_std = group_std > 0
    flagged.loc[nonzero_std, "workload_zscore"] = (
        (flagged.loc[nonzero_std, "alerts_handled"] - group_mean[nonzero_std])
        / group_std[nonzero_std]
    )
    flagged["overloaded"] = flagged["workload_zscore"] >= zscore_threshold
    return flagged
