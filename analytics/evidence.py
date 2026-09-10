"""
Evidence retrieval for SAT-SA.

Every finding must be traceable back to the underlying operational
records. This module is the drill-down layer: given an alert_id or
case_id, return everything a supervisor would need to verify the
finding by hand — the alert record itself, its full event trail,
the actions taken, evidence attached (or not), and escalation status.
"""

import pandas as pd


def evidence_bundle_for_alert(data: dict, alerts_enriched: pd.DataFrame, alert_id: str) -> dict:
    alert_row = alerts_enriched[alerts_enriched["alert_id"] == alert_id]
    if alert_row.empty:
        return {"error": f"alert_id {alert_id} not found"}

    events = data["alert_events"][data["alert_events"]["alert_id"] == alert_id].sort_values("timestamp")
    actions = data["actions"][data["actions"]["alert_id"] == alert_id].sort_values("timestamp")
    evidence = data["evidence"][data["evidence"]["alert_id"] == alert_id] if not data["evidence"].empty else pd.DataFrame()
    escalation = data["escalations"][data["escalations"]["alert_id"] == alert_id]

    return {
        "alert": alert_row.iloc[0].to_dict(),
        "lifecycle_events": events.to_dict(orient="records"),
        "analyst_actions": actions.to_dict(orient="records"),
        "evidence_records": evidence.to_dict(orient="records") if not evidence.empty else [],
        "escalation_status": escalation.iloc[0].to_dict() if not escalation.empty else None,
    }


def evidence_bundle_for_entity_findings(entity_findings: pd.DataFrame, data: dict,
                                          alerts_enriched: pd.DataFrame, top_n: int = 10) -> list:
    """
    Convenience wrapper for the report generator: returns the full
    evidence bundle for the top N findings for one entity, so the
    report can show "here are the specific records behind this score"
    rather than just an aggregate count.
    """
    bundles = []
    for alert_id in entity_findings["alert_id"].dropna().unique()[:top_n]:
        bundles.append(evidence_bundle_for_alert(data, alerts_enriched, alert_id))
    return bundles


# ---------------------------------------------------------------------------
# Finding -> source records
# ---------------------------------------------------------------------------
#
# The audit chain a supervisory finding has to support is:
#
#     Finding -> Rule -> Rationale -> Evidence -> SOURCE RECORDS
#
# Everything up to `Evidence` is produced by the rule engine. This last
# step goes back to the submission itself and returns the rows the rule
# actually read, so an examiner can verify a finding by hand rather than
# taking the tool's word for it.
#
# Findings are not all at the same grain. An alert-level rule points at
# one alert; ANALYST_OVERLOAD points at an analyst; TELEMETRY_GAP points
# at a telemetry source; REPEATED_ALERT_WITHOUT_REMEDIATION points at an
# asset. Each needs different rows pulled, so the bundle is dispatched on
# what the finding actually identifies rather than assuming an alert_id.


def _records(df) -> list:
    """DataFrame -> JSON-safe records, preserving column order."""
    if df is None or df.empty:
        return []
    clean = df.astype(object).where(pd.notnull(df), None)
    return clean.to_dict(orient="records")


def _rows_for(data: dict, table: str, column: str, value) -> list:
    frame = data.get(table)
    if frame is None or frame.empty or column not in frame.columns:
        return []
    return _records(frame[frame[column] == value])


def evidence_bundle_for_case(data: dict, case_id: str) -> dict:
    return {"case": _rows_for(data, "cases", "case_id", case_id)}


def evidence_bundle_for_analyst(data: dict, alerts_enriched: pd.DataFrame,
                                  soc_id: str, analyst_id: str,
                                  sample: int = 25) -> dict:
    """
    Records behind an analyst-grain finding.

    The alert list is a SAMPLE, and says so: an overloaded analyst may
    have handled hundreds of alerts, and returning all of them would
    bury the reviewer rather than inform them.
    """
    analysts = data.get("analysts")
    analyst_row = []
    if analysts is not None and not analysts.empty:
        analyst_row = _records(analysts[analysts["analyst_id"] == analyst_id])

    handled = pd.DataFrame()
    if alerts_enriched is not None and not alerts_enriched.empty:
        handled = alerts_enriched[
            (alerts_enriched["soc_id"] == soc_id)
            & (alerts_enriched["assigned_analyst_id"] == analyst_id)
        ]

    return {
        "analyst": analyst_row,
        "alerts_handled_total": int(len(handled)),
        "alerts_handled_sample": _records(handled.head(sample)),
    }


def evidence_bundle_for_asset(data: dict, alerts_enriched: pd.DataFrame,
                                soc_id: str, asset_id: str,
                                category=None) -> dict:
    """Every alert on one asset — the recurrence a root-cause rule flagged."""
    if alerts_enriched is None or alerts_enriched.empty:
        return {"asset_alerts": []}

    rows = alerts_enriched[
        (alerts_enriched["soc_id"] == soc_id)
        & (alerts_enriched["asset_id"] == asset_id)
    ]
    if category and "category" in rows.columns:
        rows = rows[rows["category"] == category]

    alert_ids = set(rows["alert_id"].dropna())
    cases = data.get("cases", pd.DataFrame())
    related_cases = pd.DataFrame()
    if not cases.empty and "alert_id" in cases.columns:
        related_cases = cases[cases["alert_id"].isin(alert_ids)]

    return {
        "asset_alerts": _records(rows.sort_values("timestamp_created")
                                  if "timestamp_created" in rows.columns else rows),
        "case_closures": _records(related_cases),
    }


def evidence_bundle_for_telemetry(data: dict, soc_id: str,
                                    source_name=None) -> dict:
    telemetry = data.get("telemetry")
    if telemetry is None or telemetry.empty:
        return {"telemetry": []}

    rows = telemetry[telemetry["soc_id"] == soc_id]
    if source_name and "source_name" in rows.columns:
        rows = rows[rows["source_name"] == source_name]
    return {"telemetry": _records(rows)}


def evidence_bundle_for_entity(data: dict, alerts_enriched: pd.DataFrame,
                                 soc_id: str, sample: int = 25) -> dict:
    """
    Records behind an entity-grain finding (record-keeping rules, low
    activity). Counts are exact; row lists are samples and are labelled.
    """
    soc = data.get("socs", pd.DataFrame())
    soc_row = _records(soc[soc["soc_id"] == soc_id]) if not soc.empty else []

    alerts = pd.DataFrame()
    if alerts_enriched is not None and not alerts_enriched.empty:
        alerts = alerts_enriched[alerts_enriched["soc_id"] == soc_id]

    cases = data.get("cases", pd.DataFrame())
    entity_cases = (cases[cases["soc_id"] == soc_id]
                    if not cases.empty and "soc_id" in cases.columns
                    else pd.DataFrame())

    escalations = data.get("escalations", pd.DataFrame())
    entity_escalations = pd.DataFrame()
    if not escalations.empty and not alerts.empty:
        entity_escalations = escalations[
            escalations["alert_id"].isin(set(alerts["alert_id"].dropna()))]

    return {
        "entity": soc_row,
        "alert_count": int(len(alerts)),
        "case_count": int(len(entity_cases)),
        "escalation_record_count": int(len(entity_escalations)),
        "alerts_sample": _records(alerts.head(sample)),
        "cases_sample": _records(entity_cases.head(sample)),
    }


def evidence_bundle_for_finding(data: dict, alerts_enriched: pd.DataFrame,
                                  finding: dict) -> dict:
    """
    The source records behind one finding, whatever its grain.

    Returns a dict of named sections plus `_grain`, which names what the
    finding actually points at. Nothing here interprets the finding: the
    rows are returned as they appear in the submission.
    """
    def present(value):
        return value is not None and str(value).strip() not in ("", "nan", "None", "<NA>")

    alert_id = finding.get("alert_id")
    case_id = finding.get("case_id")
    analyst_id = finding.get("assigned_analyst_id")
    soc_id = finding.get("soc_id")
    evidence = finding.get("evidence") or {}
    finding_type = str(finding.get("finding_type", ""))

    bundle: dict = {}

    if present(alert_id):
        bundle["_grain"] = "alert"
        bundle.update(evidence_bundle_for_alert(data, alerts_enriched, alert_id))
        if present(case_id):
            bundle.update(evidence_bundle_for_case(data, case_id))
        return bundle

    if finding_type == "ANALYST_OVERLOAD" and present(analyst_id):
        bundle["_grain"] = "analyst"
        bundle.update(evidence_bundle_for_analyst(
            data, alerts_enriched, soc_id, analyst_id))
        return bundle

    if finding_type == "REPEATED_ALERT_WITHOUT_REMEDIATION":
        bundle["_grain"] = "asset"
        bundle.update(evidence_bundle_for_asset(
            data, alerts_enriched, soc_id,
            evidence.get("asset_id"), evidence.get("category")))
        return bundle

    if finding_type == "TELEMETRY_GAP":
        bundle["_grain"] = "telemetry source"
        bundle.update(evidence_bundle_for_telemetry(
            data, soc_id,
            evidence.get("source_name") or finding.get("source_name")))
        return bundle

    if present(case_id):
        bundle["_grain"] = "case"
        bundle.update(evidence_bundle_for_case(data, case_id))
        return bundle

    bundle["_grain"] = "entity"
    bundle.update(evidence_bundle_for_entity(data, alerts_enriched, soc_id))
    return bundle
