"""
Normalizer for SAT-SA.

Single responsibility: turn raw loaded tables into an analysis-ready
shape. This is where alert_events gets pivoted into per-alert timing
fields (ack_delay_minutes, investigation_duration_minutes, etc.) so
every detector downstream can work off a single flat alerts_enriched
table instead of re-deriving timing logic independently.
"""

import pandas as pd

DATETIME_COLUMNS = {
    "alerts": ["timestamp_created"],
    "alert_events": ["timestamp"],
    "cases": ["created_at", "closed_at"],
    "incidents": ["created_at", "contained_at", "resolved_at"],
    "escalations": ["initiated_at", "completed_at"],
    "actions": ["timestamp"],
    "evidence": ["timestamp"],
    "telemetry": ["last_event_timestamp"],
}

BOOLEAN_COLUMNS = {
    "alerts": ["true_positive"],
    "escalations": ["required", "initiated", "completed"],
    "telemetry": ["expected", "enabled"],
    "analysts": ["active"],
}


def _parse_datetimes(df: pd.DataFrame, columns) -> pd.DataFrame:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    return df


def _parse_booleans(df: pd.DataFrame, columns) -> pd.DataFrame:
    for col in columns:
        if col in df.columns and df[col].dtype != bool:
            df[col] = df[col].astype(str).str.strip().str.lower().map(
                {"true": True, "false": False, "1": True, "0": False}
            ).fillna(df[col])
    return df


def normalize_dataset(data: dict) -> dict:
    """
    Parses datetimes/booleans in place and returns the same dict.
    Does not add derived columns — that happens in build_alerts_enriched.
    """
    for table, cols in DATETIME_COLUMNS.items():
        if table in data and not data[table].empty:
            data[table] = _parse_datetimes(data[table], cols)
    for table, cols in BOOLEAN_COLUMNS.items():
        if table in data and not data[table].empty:
            data[table] = _parse_booleans(data[table], cols)
    return data


def build_alerts_enriched(data: dict) -> pd.DataFrame:
    """
    The core join every detector should use. Produces one row per
    alert with lifecycle timing derived from alert_events, escalation
    status from escalations, and evidence presence from evidence.

    Added columns:
        ack_delay_minutes            time from CREATED to ACKNOWLEDGED
        investigation_duration_minutes  time from INVESTIGATION_STARTED to first CLOSED
        was_reopened                  bool, any REOPENED event present
        has_evidence                  bool, any evidence row for this alert
        escalation_required           from escalations.required
        escalation_initiated          from escalations.initiated
        escalation_delay_minutes      from escalations.delay_minutes
    """
    alerts = data["alerts"].copy()
    events = data["alert_events"].copy()
    escalations = data.get("escalations", pd.DataFrame())
    evidence = data.get("evidence", pd.DataFrame())

    if events.empty:
        alerts["ack_delay_minutes"] = pd.NA
        alerts["investigation_duration_minutes"] = pd.NA
        alerts["was_reopened"] = False
    else:
        pivots = []
        for alert_id, group in events.groupby("alert_id"):
            group = group.sort_values("timestamp")
            created = group.loc[group.event_type == "CREATED", "timestamp"].min()
            acked = group.loc[group.event_type == "ACKNOWLEDGED", "timestamp"].min()
            invest_start = group.loc[group.event_type == "INVESTIGATION_STARTED", "timestamp"].min()
            first_closed = group.loc[group.event_type == "CLOSED", "timestamp"].min()
            reopened = (group.event_type == "REOPENED").any()

            ack_delay = ((acked - created).total_seconds() / 60
                         if pd.notna(acked) and pd.notna(created) else pd.NA)
            invest_duration = ((first_closed - invest_start).total_seconds() / 60
                                if pd.notna(first_closed) and pd.notna(invest_start) else pd.NA)

            pivots.append({
                "alert_id": alert_id,
                "ack_delay_minutes": ack_delay,
                "investigation_duration_minutes": invest_duration,
                "was_reopened": reopened,
            })

        pivot_df = pd.DataFrame(pivots)
        alerts = alerts.merge(pivot_df, on="alert_id", how="left")

    if evidence.empty or "alert_id" not in evidence.columns:
        alerts["has_evidence"] = False
    else:
        has_evidence_ids = set(evidence["alert_id"].dropna())
        alerts["has_evidence"] = alerts["alert_id"].isin(has_evidence_ids)

    cases = data.get("cases", pd.DataFrame())
    if not cases.empty and "alert_id" in cases.columns:
        alerts = alerts.merge(
            cases[["alert_id", "case_id"]], on="alert_id", how="left"
        )
    else:
        alerts["case_id"] = pd.NA

    if escalations.empty:
        alerts["escalation_required"] = pd.NA
        alerts["escalation_initiated"] = pd.NA
        alerts["escalation_delay_minutes"] = pd.NA
    else:
        esc_cols = escalations[["alert_id", "required", "initiated", "delay_minutes"]].rename(
            columns={
                "required": "escalation_required",
                "initiated": "escalation_initiated",
                "delay_minutes": "escalation_delay_minutes",
            }
        )
        alerts = alerts.merge(esc_cols, on="alert_id", how="left")

    return alerts
