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
