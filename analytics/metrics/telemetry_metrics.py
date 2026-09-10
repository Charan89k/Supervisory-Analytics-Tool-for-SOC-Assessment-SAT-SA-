"""Telemetry coverage metrics, per SOC — the raw material for negative-space detection."""

import pandas as pd


def telemetry_summary_by_soc(telemetry: pd.DataFrame) -> pd.DataFrame:
    return (telemetry.groupby("soc_id")
            .agg(sources_total=("telemetry_id", "count"),
                 sources_expected=("expected", "sum"),
                 sources_missing=("health_status", lambda s: (s == "MISSING").sum()),
                 avg_coverage_pct=("coverage_percentage", "mean"))
            .reset_index())


def expected_source_gaps(telemetry: pd.DataFrame, min_coverage_pct: float = 70) -> pd.DataFrame:
    """Every expected telemetry source below the coverage threshold, one row each."""
    expected = telemetry[telemetry["expected"] == True]  # noqa: E712
    gaps = expected[expected["coverage_percentage"] < min_coverage_pct]
    return gaps[["soc_id", "source_name", "source_type", "coverage_percentage", "health_status"]]
