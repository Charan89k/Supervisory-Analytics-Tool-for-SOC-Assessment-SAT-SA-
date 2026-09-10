"""Escalation compliance metrics, per SOC."""

import pandas as pd


def escalation_compliance_by_soc(alerts_enriched: pd.DataFrame) -> pd.DataFrame:
    """
    For alerts where escalation was required by policy, what fraction
    were actually initiated? This is the single clearest execution-gap
    signal in the whole dataset.
    """
    required = alerts_enriched[alerts_enriched["escalation_required"] == True]  # noqa: E712
    if required.empty:
        return pd.DataFrame(columns=["soc_id", "required_count", "initiated_count",
                                      "compliance_rate", "avg_escalation_delay_minutes"])

    grouped = required.groupby("soc_id").agg(
        required_count=("alert_id", "count"),
        initiated_count=("escalation_initiated", "sum"),
        avg_escalation_delay_minutes=("escalation_delay_minutes", "mean"),
    ).reset_index()
    grouped["compliance_rate"] = grouped["initiated_count"] / grouped["required_count"]
    return grouped
