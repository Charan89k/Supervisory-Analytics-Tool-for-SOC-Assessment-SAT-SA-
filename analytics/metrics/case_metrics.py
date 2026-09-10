"""Case-management metrics, per SOC."""

import pandas as pd


def case_resolution_time_by_soc(cases: pd.DataFrame) -> pd.DataFrame:
    df = cases.copy()
    df["resolution_minutes"] = (df["closed_at"] - df["created_at"]).dt.total_seconds() / 60
    return (df.groupby("soc_id")["resolution_minutes"]
            .agg(["mean", "median", "std", "min", "max"])
            .add_prefix("resolution_minutes_")
            .reset_index())


def reopen_rate_by_soc(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    return (alerts_enriched.groupby("soc_id")["was_reopened"]
            .mean().rename("reopen_rate").reset_index())


def resolution_outcome_mix_by_soc(cases: pd.DataFrame) -> pd.DataFrame:
    mix = cases.groupby(["soc_id", "resolution"]).size().unstack(fill_value=0)
    mix.columns = [f"count_{c.lower()}" for c in mix.columns]
    return mix.reset_index()
