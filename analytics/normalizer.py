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
        was_acknowledged              bool, any ACKNOWLEDGED event present
        investigation_started         bool, any INVESTIGATION_STARTED event present
        was_closed                    bool, any CLOSED event present
        was_reopened                  bool, any REOPENED event present
        has_evidence                  bool, any evidence row for this alert
        escalation_required           from escalations.required
        escalation_initiated          from escalations.initiated
        escalation_delay_minutes      from escalations.delay_minutes

    was_acknowledged / investigation_started / was_closed record the
    PRESENCE of a lifecycle event, which is a different question from
    the duration between two events. A duration is null both when an
    alert was never investigated and when its timestamps are unusable,
    so a duration alone cannot distinguish "no investigation happened"
    from "we cannot tell" — and ACK_WITHOUT_INVESTIGATION turns on
    exactly that distinction.
    """
    alerts = data["alerts"].copy()
    events = data["alert_events"].copy()
    escalations = data.get("escalations", pd.DataFrame())
    evidence = data.get("evidence", pd.DataFrame())

    if events.empty:
        alerts["ack_delay_minutes"] = pd.NA
        alerts["investigation_duration_minutes"] = pd.NA
        alerts["was_acknowledged"] = False
        alerts["investigation_started"] = False
        alerts["was_closed"] = False
        alerts["was_reopened"] = False
    else:
        # Vectorised event pivot.
        #
        # This was a Python loop over one group per alert, which cost
        # ~9.6s of an 11.6s pipeline run on 3,300 alerts — 83% of total
        # runtime — and scaled linearly, putting the PS requirement to
        # "support analysis of large datasets" out of reach: ~100k
        # alerts would have taken minutes before a single rule ran.
        #
        # Taking the first timestamp per (alert_id, event_type) in one
        # grouped aggregation and unstacking to columns produces exactly
        # the same values. Sorting inside each group was never needed —
        # min() does not care about order.
        first_event = (
            events.groupby(["alert_id", "event_type"])["timestamp"]
            .min()
            .unstack("event_type")
        )

        def event_column(name):
            """First timestamp of `name` per alert; NaT where absent."""
            if name in first_event.columns:
                return first_event[name]
            return pd.Series(pd.NaT, index=first_event.index, dtype="datetime64[ns, UTC]")

        created = event_column("CREATED")
        acked = event_column("ACKNOWLEDGED")
        invest_start = event_column("INVESTIGATION_STARTED")
        first_closed = event_column("CLOSED")
        reopened = event_column("REOPENED")

        pivot_df = pd.DataFrame({
            "ack_delay_minutes": (acked - created).dt.total_seconds() / 60,
            "investigation_duration_minutes":
                (first_closed - invest_start).dt.total_seconds() / 60,
            "was_acknowledged": acked.notna(),
            "investigation_started": invest_start.notna(),
            "was_closed": first_closed.notna(),
            "was_reopened": reopened.notna(),
        }).reset_index()

        alerts = alerts.merge(pivot_df, on="alert_id", how="left")

        # Alerts with no event rows at all get False, not NaN: absence
        # of an event trail is itself "not acknowledged / not
        # investigated / not closed", and a NaN here would silently
        # drop the row out of every boolean mask downstream.
        for flag in ("was_acknowledged", "investigation_started",
                     "was_closed", "was_reopened"):
            alerts[flag] = alerts[flag].fillna(False).astype(bool)

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
