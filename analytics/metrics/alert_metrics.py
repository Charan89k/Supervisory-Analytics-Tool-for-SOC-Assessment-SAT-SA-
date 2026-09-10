"""Alert-level descriptive metrics, per SOC."""

import pandas as pd


def alert_volume_by_soc(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    return (alerts_enriched.groupby("soc_id")
            .agg(alert_count=("alert_id", "count"),
                 avg_risk_score=("risk_score", "mean"))
            .reset_index())


def severity_mix_by_soc(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    mix = (alerts_enriched.groupby(["soc_id", "severity"])
           .size().unstack(fill_value=0))
    mix.columns = [f"count_{c.lower()}" for c in mix.columns]
    return mix.reset_index()


def true_positive_rate_by_soc(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    df = alerts_enriched.copy()
    if df["true_positive"].dtype != bool:
        df["true_positive"] = df["true_positive"].astype(str).str.lower() == "true"
    return (df.groupby("soc_id")["true_positive"]
            .mean().rename("true_positive_rate").reset_index())


def category_coverage_by_soc(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    """Which alert categories each SOC actually generated during the period."""
    return (alerts_enriched.groupby("soc_id")["category"]
            .agg(lambda s: set(s.unique())).rename("categories_observed").reset_index())
