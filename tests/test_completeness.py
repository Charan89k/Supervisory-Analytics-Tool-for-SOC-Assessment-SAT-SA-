"""
Evidence completeness.

The risk score has a structural blind spot: most execution-gap rules
read a record and ask whether it shows the right thing happened, so a
record that was never written produces no finding. An entity keeping
poor records therefore accumulates fewer findings than one keeping good
records over identical behaviour — and scores better.

Completeness publishes the denominator instead of reweighting the
model. These tests hold both halves: it must expose the suppression,
and it must not touch the score.
"""

import pandas as pd
import pytest

from analytics.completeness import (
    DEFAULT_MIN_COVERAGE,
    Coverage,
    EntityCompleteness,
    assess_completeness,
)


def alerts(rows):
    """(alert_id, soc_id, severity, has_escalation_record, case_id)."""
    return pd.DataFrame([
        {"alert_id": a, "soc_id": s, "severity": sev,
         "escalation_required": (True if esc else None),
         "escalation_initiated": False,
         "case_id": case}
        for a, s, sev, esc, case in rows
    ])


def cases(rows):
    """(case_id, soc_id, investigation_notes)."""
    return pd.DataFrame([
        {"case_id": c, "soc_id": s, "investigation_notes": notes}
        for c, s, notes in rows
    ])


def events(alert_ids):
    return pd.DataFrame([{"alert_id": a, "event_type": "CREATED"}
                          for a in alert_ids])


# ----------------------------------------------------------------------
# It exposes the suppression
# ----------------------------------------------------------------------

def test_absent_escalation_records_are_reported(): 
    """
    Ten HIGH alerts, two with an escalation record. MISSED_ESCALATION
    can only fire on those two; the other eight are invisible to it.
    """
    rows = [(f"A{i}", "SOC-1", "HIGH", i < 2, f"C{i}") for i in range(10)]
    result = assess_completeness(alerts(rows),
                                  cases([(f"C{i}", "SOC-1", "note") for i in range(10)]),
                                  events([f"A{i}" for i in range(10)]))

    coverage = next(c for c in result["SOC-1"].coverages
                    if c.key == "escalation_records")
    assert coverage.present == 2
    assert coverage.total == 10
    assert coverage.fraction == pytest.approx(0.2)
    assert coverage.limited()
    assert "MISSED_ESCALATION" in result["SOC-1"].suppressed_rules


def test_absent_investigation_notes_are_reported():
    rows = [(f"A{i}", "SOC-1", "HIGH", True, f"C{i}") for i in range(10)]
    note_rows = [(f"C{i}", "SOC-1", "note" if i < 2 else "") for i in range(10)]
    result = assess_completeness(alerts(rows), cases(note_rows),
                                  events([f"A{i}" for i in range(10)]))

    assert "REPETITIVE_INVESTIGATION" in result["SOC-1"].suppressed_rules


def test_a_complete_submission_is_not_flagged():
    rows = [(f"A{i}", "SOC-1", "HIGH", True, f"C{i}") for i in range(10)]
    note_rows = [(f"C{i}", "SOC-1", "specific note") for i in range(10)]
    result = assess_completeness(alerts(rows), cases(note_rows),
                                  events([f"A{i}" for i in range(10)]))

    entity = result["SOC-1"]
    assert entity.suppressed_rules == []
    assert entity.caveat() == ""
    assert entity.overall == pytest.approx(1.0)


def test_the_caveat_names_the_rules_it_limits():
    rows = [(f"A{i}", "SOC-1", "HIGH", False, f"C{i}") for i in range(10)]
    note_rows = [(f"C{i}", "SOC-1", "") for i in range(10)]
    caveat = assess_completeness(alerts(rows), cases(note_rows),
                                  events([f"A{i}" for i in range(10)])
                                  )["SOC-1"].caveat()

    assert "MISSED_ESCALATION" in caveat
    assert "REPETITIVE_INVESTIGATION" in caveat
    assert "floor" in caveat


def test_the_caveat_does_not_accuse():
    """
    Incomplete records are a reason to ask a question. They may be an
    export problem. The wording must not convert that into a finding.
    """
    rows = [(f"A{i}", "SOC-1", "HIGH", False, f"C{i}") for i in range(10)]
    caveat = assess_completeness(
        alerts(rows), cases([(f"C{i}", "SOC-1", "") for i in range(10)]),
        events([f"A{i}" for i in range(10)]))["SOC-1"].caveat()

    lowered = caveat.lower()
    for accusation in ("failed", "negligent", "concealed", "hiding",
                        "deliberately", "misconduct", "non-compliant"):
        assert accusation not in lowered, accusation
    assert "incomplete export" in lowered or "stores them elsewhere" in lowered


def test_entities_are_measured_independently():
    rows = ([(f"A{i}", "SOC-1", "HIGH", False, f"C{i}") for i in range(10)]
            + [(f"B{i}", "SOC-2", "HIGH", True, f"D{i}") for i in range(10)])
    note_rows = ([(f"C{i}", "SOC-1", "") for i in range(10)]
                 + [(f"D{i}", "SOC-2", "note") for i in range(10)])
    result = assess_completeness(alerts(rows), cases(note_rows),
                                  events([r[0] for r in rows]))

    assert result["SOC-1"].suppressed_rules
    assert result["SOC-2"].suppressed_rules == []


# ----------------------------------------------------------------------
# It is not a score
# ----------------------------------------------------------------------

def test_nothing_to_measure_is_none_not_zero():
    """
    An entity with no HIGH/CRITICAL alerts has no escalation records to
    be missing. Reporting 0% would invent a gap.
    """
    rows = [(f"A{i}", "SOC-1", "LOW", False, f"C{i}") for i in range(5)]
    result = assess_completeness(alerts(rows),
                                  cases([(f"C{i}", "SOC-1", "n") for i in range(5)]),
                                  events([f"A{i}" for i in range(5)]))

    coverage = next(c for c in result["SOC-1"].coverages
                    if c.key == "escalation_records")
    assert coverage.total == 0
    assert coverage.fraction is None
    assert not coverage.limited()


def test_overall_ignores_measures_that_could_not_be_taken():
    entity = EntityCompleteness(soc_id="SOC-1", coverages=[
        Coverage("a", "A", 4, 10, ("R1",), ""),     # below threshold
        Coverage("b", "B", 0, 0, ("R2",), ""),      # nothing to measure
    ])
    # 0.4 alone, not 0.2 — the unmeasurable one is excluded rather than
    # counted as zero, which would invent a gap that was never there.
    assert entity.overall == pytest.approx(0.4)
    assert entity.suppressed_rules == ["R1"]


def test_coverage_exactly_at_the_threshold_is_not_limited():
    """`limited` means below the bar, not at it."""
    assert not Coverage("a", "A", 5, 10, ("R1",), "").limited(0.5)
    assert Coverage("a", "A", 4, 10, ("R1",), "").limited(0.5)


def test_an_empty_submission_yields_nothing():
    assert assess_completeness(pd.DataFrame(), pd.DataFrame(),
                                pd.DataFrame()) == {}


def test_threshold_is_configurable():
    rows = [(f"A{i}", "SOC-1", "HIGH", i < 6, f"C{i}") for i in range(10)]
    note_rows = [(f"C{i}", "SOC-1", "note") for i in range(10)]
    data = (alerts(rows), cases(note_rows), events([f"A{i}" for i in range(10)]))

    lenient = assess_completeness(*data, threshold=0.5)
    strict = assess_completeness(*data, threshold=0.8)

    assert "MISSED_ESCALATION" not in lenient["SOC-1"].suppressed_rules
    assert "MISSED_ESCALATION" in strict["SOC-1"].suppressed_rules


def test_default_threshold_is_a_half():
    assert DEFAULT_MIN_COVERAGE == 0.5


# ----------------------------------------------------------------------
# End to end: it must not touch the assessment
# ----------------------------------------------------------------------

import json
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")


@pytest.mark.skipif(not os.path.isdir(DATA),
                    reason="synthetic dataset not generated")
def test_completeness_is_published_for_every_entity(assessment):
    for entity in assessment["entities"]:
        completeness = entity.get("evidence_completeness")
        assert completeness is not None, entity["soc_id"]
        assert completeness["soc_id"] == entity["soc_id"]
        assert set(completeness) >= {"overall_coverage", "limited",
                                      "suppressed_rules", "caveat",
                                      "coverages"}


@pytest.mark.skipif(not os.path.isdir(DATA),
                    reason="synthetic dataset not generated")
def test_it_separates_poor_recordkeeping_from_the_rest(assessment):
    """
    The case that motivated this. SOC-005 is seeded with poor
    record-keeping; its risk score (50.6) sits BELOW an entity whose
    seeded rates are worse on no dimension at all (SOC-002, 56.4).
    Completeness is what tells a supervisor why.
    """
    by_id = {e["soc_id"]: e for e in assessment["entities"]}
    if "SOC-005" not in by_id or "SOC-002" not in by_id:
        pytest.skip("reference dataset does not carry the seeded profiles")

    poor = by_id["SOC-005"]["evidence_completeness"]
    typical = by_id["SOC-002"]["evidence_completeness"]

    assert poor["limited"] is True
    assert typical["limited"] is False
    assert poor["overall_coverage"] < typical["overall_coverage"]
    assert "MISSED_ESCALATION" in poor["suppressed_rules"]


@pytest.mark.skipif(not os.path.isdir(DATA),
                    reason="synthetic dataset not generated")
def test_completeness_changes_no_score_finding_or_rank(assessment):
    """
    The guarantee the whole design rests on. Completeness is reported
    beside the risk model, never inside it — so stripping it out must
    leave the assessment bit-for-bit identical.
    """
    from main import run_pipeline

    stripped = {}
    for entity in assessment["entities"]:
        stripped[entity["soc_id"]] = {
            "rank": entity["priority_rank"],
            "score": entity["supervisory_risk_score"],
            "percentile": entity["supervisory_risk_score_percentile"],
            "execution": entity["execution_gap_count"],
            "negative": entity["negative_space_count"],
            "breakdown": entity["score_breakdown"],
        }

    with tempfile.TemporaryDirectory() as out:
        again = run_pipeline(DATA, out, CONFIG)

    for entity in again["entities"]:
        before = stripped[entity["soc_id"]]
        assert before["rank"] == entity["priority_rank"]
        assert before["score"] == entity["supervisory_risk_score"]
        assert before["breakdown"] == entity["score_breakdown"]


@pytest.mark.skipif(not os.path.isdir(DATA),
                    reason="synthetic dataset not generated")
def test_no_finding_carries_a_completeness_field(assessment):
    """
    Completeness describes a submission, not a finding. It must never
    appear inside the evidence a supervisor reads for one.
    """
    for entity in assessment["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            assert "evidence_completeness" not in finding
            assert "overall_coverage" not in finding

    for item in assessment["review_queue"]:
        assert "evidence_completeness" not in item


@pytest.mark.skipif(not os.path.isdir(DATA),
                    reason="synthetic dataset not generated")
def test_completeness_is_not_a_scored_rule(assessment):
    """It must never appear as a weighted component of the risk score."""
    for entity in assessment["entities"]:
        for component in entity["score_breakdown"]:
            assert "completeness" not in component["finding_type"]
            assert "coverage" not in component["finding_type"]
