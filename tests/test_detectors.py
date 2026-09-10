"""
Detector test suite for SAT-SA.

These tests use small, hand-constructed DataFrames with known,
controlled values — not the synthetic dataset generator — so each
rule is verified against an exact expected outcome, independent of
whatever the generator happens to produce. This is what lets us call
the rule set "validated" rather than "produces plausible-looking
numbers on one dataset."

Every rule gets both a positive case (should fire) and a negative
case (should NOT fire), per the test plan: a detector that only ever
gets tested on cases where it should fire can't tell you anything
about its false-positive rate.
"""

import os
import yaml
import pandas as pd
import pytest

from analytics.detection.execution_gaps import (
    detect_missed_escalations,
    detect_fast_closures,
    detect_slow_triage,
    detect_missing_evidence,
    detect_reopened_cases,
    detect_repetitive_investigations,
)
from analytics.detection.negative_space import detect_telemetry_gaps

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "assessment_rules.yaml")


@pytest.fixture(scope="module")
def cfg():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def make_alert(**overrides):
    """One-row alerts_enriched-shaped DataFrame with sensible defaults."""
    base = {
        "alert_id": "ALT-TEST-001",
        "case_id": "CASE-TEST-001",
        "soc_id": "SOC-TEST",
        "assigned_analyst_id": "ANL-TEST-001",
        "severity": "HIGH",
        "escalation_required": True,
        "escalation_initiated": True,
        "escalation_delay_minutes": 5.0,
        "ack_delay_minutes": 5.0,
        "investigation_duration_minutes": 60.0,
        "has_evidence": True,
        "was_reopened": False,
    }
    base.update(overrides)
    return pd.DataFrame([base])


def make_cases(rows):
    """rows: list of dicts, each a case row."""
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# MISSED_ESCALATION
# ---------------------------------------------------------------------------

def test_missed_escalation_fires_when_required_and_not_initiated():
    alerts = make_alert(severity="HIGH", escalation_required=True, escalation_initiated=False)
    result = detect_missed_escalations(alerts)
    assert len(result) == 1
    assert result.iloc[0]["finding_type"] == "MISSED_ESCALATION"
    assert result.iloc[0]["alert_id"] == "ALT-TEST-001"


def test_missed_escalation_does_not_fire_when_escalated():
    alerts = make_alert(severity="HIGH", escalation_required=True, escalation_initiated=True)
    result = detect_missed_escalations(alerts)
    assert result.empty


def test_missed_escalation_does_not_fire_when_not_required():
    alerts = make_alert(severity="LOW", escalation_required=False, escalation_initiated=False)
    result = detect_missed_escalations(alerts)
    assert result.empty


# ---------------------------------------------------------------------------
# SLOW_TRIAGE  (HIGH: SLA=15min, multiplier=2.5x -> threshold=37.5min)
# ---------------------------------------------------------------------------

def test_slow_triage_fires_above_threshold(cfg):
    alerts = make_alert(severity="HIGH", ack_delay_minutes=45.0)
    result = detect_slow_triage(alerts, cfg)
    assert len(result) == 1
    assert result.iloc[0]["finding_type"] == "SLOW_TRIAGE"
    assert result.iloc[0]["evidence"]["ack_delay_minutes"] == 45.0
    assert result.iloc[0]["evidence"]["threshold_minutes"] == 37.5


def test_slow_triage_does_not_fire_at_normal_ack_delay(cfg):
    alerts = make_alert(severity="HIGH", ack_delay_minutes=10.0)
    result = detect_slow_triage(alerts, cfg)
    assert result.empty


def test_slow_triage_does_not_fire_exactly_at_threshold(cfg):
    # exactly at 37.5 should NOT fire — the rule is strictly greater-than
    alerts = make_alert(severity="HIGH", ack_delay_minutes=37.5)
    result = detect_slow_triage(alerts, cfg)
    assert result.empty


# ---------------------------------------------------------------------------
# FAST_CLOSURE  (HIGH: SLA=90min, fraction=0.15 -> threshold=13.5min, margin=5min)
# ---------------------------------------------------------------------------

def test_fast_closure_fires_on_clearly_premature_closure(cfg):
    alerts = make_alert(severity="HIGH", investigation_duration_minutes=3.0)
    result = detect_fast_closures(alerts, cfg)
    assert len(result) == 1
    assert result.iloc[0]["finding_type"] == "FAST_CLOSURE"
    assert result.iloc[0]["evidence"]["deviation_minutes"] >= 5


def test_fast_closure_does_not_fire_on_borderline_case(cfg):
    # 13.0 min vs 13.5 min threshold = only 0.5 min deviation, below the
    # 5-minute margin. This is the exact case flagged as a false positive
    # during manual review and fixed via fast_closure_min_margin_minutes.
    alerts = make_alert(severity="HIGH", investigation_duration_minutes=13.0)
    result = detect_fast_closures(alerts, cfg)
    assert result.empty, "borderline closure (0.5 min under threshold) must not fire"


def test_fast_closure_does_not_fire_on_normal_duration(cfg):
    alerts = make_alert(severity="HIGH", investigation_duration_minutes=60.0)
    result = detect_fast_closures(alerts, cfg)
    assert result.empty


def test_fast_closure_ignores_low_medium_severity(cfg):
    alerts = make_alert(severity="MEDIUM", investigation_duration_minutes=1.0)
    result = detect_fast_closures(alerts, cfg)
    assert result.empty, "FAST_CLOSURE only applies to HIGH/CRITICAL by design"


# ---------------------------------------------------------------------------
# MISSING_EVIDENCE
# ---------------------------------------------------------------------------

def test_missing_evidence_fires_when_no_evidence_on_high_severity():
    alerts = make_alert(severity="HIGH", has_evidence=False)
    result = detect_missing_evidence(alerts)
    assert len(result) == 1
    assert result.iloc[0]["finding_type"] == "MISSING_EVIDENCE"


def test_missing_evidence_does_not_fire_when_evidence_present():
    alerts = make_alert(severity="HIGH", has_evidence=True)
    result = detect_missing_evidence(alerts)
    assert result.empty


def test_missing_evidence_ignores_low_medium_severity():
    alerts = make_alert(severity="LOW", has_evidence=False)
    result = detect_missing_evidence(alerts)
    assert result.empty, "MISSING_EVIDENCE only applies to HIGH/CRITICAL by design"


# ---------------------------------------------------------------------------
# REOPENED_CASE
# ---------------------------------------------------------------------------

def test_reopened_case_fires_when_reopened():
    alerts = make_alert(was_reopened=True)
    result = detect_reopened_cases(alerts)
    assert len(result) == 1
    assert result.iloc[0]["finding_type"] == "REOPENED_CASE"


def test_reopened_case_does_not_fire_when_not_reopened():
    alerts = make_alert(was_reopened=False)
    result = detect_reopened_cases(alerts)
    assert result.empty


# ---------------------------------------------------------------------------
# REPETITIVE_INVESTIGATION  (min_occurrences=3, exact text match)
# ---------------------------------------------------------------------------

def test_repetitive_investigation_fires_on_exact_duplicate_notes(cfg):
    cases = make_cases([
        {"case_id": f"CASE-{i}", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-001",
         "severity": "MEDIUM", "alert_id": f"ALT-{i}",
         "investigation_notes": "Reviewed and closed."}
        for i in range(4)
    ])
    result = detect_repetitive_investigations(cases, cfg)
    assert len(result) == 4
    assert (result["finding_type"] == "REPETITIVE_INVESTIGATION").all()


def test_repetitive_investigation_does_not_fire_below_min_occurrences(cfg):
    # only 2 duplicates, below min_occurrences=3
    cases = make_cases([
        {"case_id": f"CASE-{i}", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-001",
         "severity": "MEDIUM", "alert_id": f"ALT-{i}",
         "investigation_notes": "Reviewed and closed."}
        for i in range(2)
    ])
    result = detect_repetitive_investigations(cases, cfg)
    assert result.empty


def test_repetitive_investigation_does_not_fire_on_unique_notes(cfg):
    cases = make_cases([
        {"case_id": f"CASE-{i}", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-001",
         "severity": "MEDIUM", "alert_id": f"ALT-{i}",
         "investigation_notes": f"Reviewed asset HOST-{1000+i} for suspicious activity."}
        for i in range(5)
    ])
    result = detect_repetitive_investigations(cases, cfg)
    assert result.empty, "genuinely distinct, alert-specific notes must not be flagged"


def test_repetitive_investigation_does_not_confuse_different_analysts(cfg):
    # same note text, but split across 2 different analysts, 2 cases each
    # -> below min_occurrences per analyst, should not fire for either
    cases = make_cases([
        {"case_id": "CASE-1", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-001",
         "severity": "MEDIUM", "alert_id": "ALT-1", "investigation_notes": "Reviewed and closed."},
        {"case_id": "CASE-2", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-001",
         "severity": "MEDIUM", "alert_id": "ALT-2", "investigation_notes": "Reviewed and closed."},
        {"case_id": "CASE-3", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-002",
         "severity": "MEDIUM", "alert_id": "ALT-3", "investigation_notes": "Reviewed and closed."},
        {"case_id": "CASE-4", "soc_id": "SOC-TEST", "assigned_analyst_id": "ANL-002",
         "severity": "MEDIUM", "alert_id": "ALT-4", "investigation_notes": "Reviewed and closed."},
    ])
    result = detect_repetitive_investigations(cases, cfg)
    assert result.empty, "repetition must be counted per-analyst, not dataset-wide"


# ---------------------------------------------------------------------------
# TELEMETRY_GAP (negative space)
# ---------------------------------------------------------------------------

def test_telemetry_gap_fires_below_coverage_threshold(cfg):
    telemetry = pd.DataFrame([{
        "telemetry_id": "TEL-001", "soc_id": "SOC-TEST", "source_name": "EDR",
        "source_type": "ENDPOINT", "expected": True, "enabled": False,
        "coverage_percentage": 0.0, "health_status": "MISSING",
    }])
    result = detect_telemetry_gaps(telemetry, cfg)
    assert len(result) == 1
    assert result.iloc[0]["finding_type"] == "TELEMETRY_GAP"


def test_telemetry_gap_does_not_fire_with_healthy_coverage(cfg):
    telemetry = pd.DataFrame([{
        "telemetry_id": "TEL-001", "soc_id": "SOC-TEST", "source_name": "EDR",
        "source_type": "ENDPOINT", "expected": True, "enabled": True,
        "coverage_percentage": 95.0, "health_status": "HEALTHY",
    }])
    result = detect_telemetry_gaps(telemetry, cfg)
    assert result.empty


def test_telemetry_gap_ignores_unexpected_sources(cfg):
    # a source that isn't expected in the first place shouldn't be
    # flagged just for having low coverage
    telemetry = pd.DataFrame([{
        "telemetry_id": "TEL-001", "soc_id": "SOC-TEST", "source_name": "OT Historian",
        "source_type": "OT", "expected": False, "enabled": False,
        "coverage_percentage": 0.0, "health_status": "MISSING",
    }])
    result = detect_telemetry_gaps(telemetry, cfg)
    assert result.empty
