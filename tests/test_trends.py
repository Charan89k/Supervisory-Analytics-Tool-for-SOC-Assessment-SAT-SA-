"""
Trend analysis tests.

Two properties matter more than any other here, and most of these tests
defend one of them:

  1. A trend must not be fabricated. A single period yields nothing; a
     series that wandered is reported as wandering; an entity that did
     not submit in a period is not recorded as having zero findings.
  2. A trend must be faithful to what was actually assessed. Each period
     is a full independent assessment, and the comparison must reproduce
     those numbers rather than derive new ones.
"""

import pytest

from analytics.trends import (
    STABLE_BAND,
    TREND_CONSISTENCY,
    Series,
    build_trends,
    persistent_findings,
)


def entity(soc_id="SOC-1", score=10.0, rank=1, execution=0, negative=0,
            types=()):
    findings = [{"finding_type": t} for t in types]
    execution_findings = findings[:execution] or [
        {"finding_type": "SLOW_TRIAGE"} for _ in range(execution)]
    return {
        "soc_id": soc_id,
        "priority_rank": rank,
        "supervisory_risk_score": score,
        "execution_gap_findings": execution_findings,
        "negative_space_findings": [
            {"finding_type": "TELEMETRY_GAP"} for _ in range(negative)],
    }


def period(label, entities):
    return (label, {"entities": entities})


# ---------------------------------------------------------------------------
# Never fabricate
# ---------------------------------------------------------------------------

def test_a_single_period_yields_no_trend():
    report = build_trends([period("2026-Q1", [entity()])])
    assert report.available is False
    assert "no trend data" in report.message.lower()
    assert report.rank_changes == []


def test_no_periods_at_all_is_reported_not_crashed():
    report = build_trends([])
    assert report.available is False
    assert report.message


def test_a_wandering_series_is_reported_as_mixed():
    """
    The defect this rule exists for. Two entities seeded as UNCHANGED
    produced net changes of -6.4 and -7.3 points from sampling alone.
    Judged on net change they read as falling; judged on directional
    consistency they read as mixed, which is what they are.
    """
    series = Series(key="risk_score", label="x",
                     periods=["a", "b", "c", "d"],
                     values=[57.3, 27.1, 56.2, 50.0])
    assert abs(series.change) > STABLE_BAND
    assert series.consistency < TREND_CONSISTENCY
    assert series.direction == "mixed"
    assert series.is_trend is False
    assert "no consistent direction" in series.change_label()


def test_a_monotonic_series_is_a_trend():
    series = Series(key="risk_score", label="x",
                     periods=["a", "b", "c", "d"],
                     values=[55.4, 82.2, 103.1, 132.6])
    assert series.consistency == pytest.approx(1.0)
    assert series.direction == "up"
    assert series.is_trend is True


def test_a_falling_monotonic_series_is_a_trend():
    series = Series(key="risk_score", label="x", periods=list("abcd"),
                     values=[134.0, 113.9, 90.8, 61.0])
    assert series.direction == "down"
    assert series.is_trend is True


def test_an_unchanged_series_is_stable():
    series = Series(key="risk_score", label="x", periods=list("abc"),
                     values=[50.0, 50.0, 50.0])
    assert series.direction == "stable"
    assert series.is_trend is False


def test_one_contrary_step_still_counts_as_a_trend():
    """A trend need not be perfectly monotonic to be real."""
    series = Series(key="x", label="x", periods=list("abcde"),
                     values=[10.0, 20.0, 18.0, 30.0, 40.0])
    assert series.consistency >= TREND_CONSISTENCY
    assert series.direction == "up"


def test_an_entity_absent_from_a_period_is_not_recorded_as_zero():
    """
    An entity that did not submit is not an entity with no findings.
    Conflating them would invent an improvement it never made.
    """
    report = build_trends([
        period("Q1", [entity("SOC-1", score=50.0), entity("SOC-2", score=20.0)]),
        period("Q2", [entity("SOC-1", score=52.0)]),
    ])
    series = report.risk_score["SOC-2"]
    assert series.values == [20.0]
    assert series.periods == ["Q1"]
    assert series.change is None
    assert "single period" in series.change_label()


# ---------------------------------------------------------------------------
# Faithful to the assessments
# ---------------------------------------------------------------------------

def test_series_reproduce_the_per_period_scores():
    report = build_trends([
        period("Q1", [entity("SOC-1", score=10.0)]),
        period("Q2", [entity("SOC-1", score=20.0)]),
        period("Q3", [entity("SOC-1", score=30.0)]),
    ])
    assert report.risk_score["SOC-1"].values == [10.0, 20.0, 30.0]
    assert report.risk_score["SOC-1"].periods == ["Q1", "Q2", "Q3"]
    assert report.risk_score["SOC-1"].change == 20.0


def test_finding_counts_track_execution_and_negative_separately():
    report = build_trends([
        period("Q1", [entity("SOC-1", execution=3, negative=1)]),
        period("Q2", [entity("SOC-1", execution=5, negative=0)]),
    ])
    assert report.execution_gaps["SOC-1"].values == [3.0, 5.0]
    assert report.negative_space["SOC-1"].values == [1.0, 0.0]
    assert report.total_findings["SOC-1"].values == [4.0, 5.0]


def test_portfolio_totals_sum_across_entities():
    report = build_trends([
        period("Q1", [entity("SOC-1", score=10.0), entity("SOC-2", score=5.0)]),
        period("Q2", [entity("SOC-1", score=20.0), entity("SOC-2", score=5.0)]),
    ])
    assert report.portfolio["risk_score"].values == [15.0, 25.0]


def test_periods_are_recorded_in_order():
    report = build_trends([
        period("2025-Q3", [entity()]), period("2025-Q4", [entity()]),
        period("2026-Q1", [entity()]),
    ])
    assert report.periods == ["2025-Q3", "2025-Q4", "2026-Q1"]


# ---------------------------------------------------------------------------
# Ranking movement
# ---------------------------------------------------------------------------

def test_rank_change_direction_is_stated_from_the_supervisors_view():
    """Moving toward rank 1 is moving toward more attention, i.e. worse."""
    report = build_trends([
        period("Q1", [entity("SOC-1", rank=3), entity("SOC-2", rank=1)]),
        period("Q2", [entity("SOC-1", rank=1), entity("SOC-2", rank=3)]),
    ])
    changes = {r.soc_id: r for r in report.rank_changes}

    assert changes["SOC-1"].change == 2
    assert "worse" in changes["SOC-1"].label
    assert changes["SOC-2"].change == -2
    assert "better" in changes["SOC-2"].label


def test_unchanged_rank_says_so():
    report = build_trends([
        period("Q1", [entity("SOC-1", rank=2)]),
        period("Q2", [entity("SOC-1", rank=2)]),
    ])
    assert "unchanged at #2" in report.rank_changes[0].label


def test_rank_changes_skip_entities_missing_from_an_endpoint():
    report = build_trends([
        period("Q1", [entity("SOC-1", rank=1), entity("SOC-2", rank=2)]),
        period("Q2", [entity("SOC-1", rank=1)]),
    ])
    assert [r.soc_id for r in report.rank_changes] == ["SOC-1"]


# ---------------------------------------------------------------------------
# Repeated and persistent findings
# ---------------------------------------------------------------------------

def test_a_finding_in_one_period_only_is_not_repeated():
    report = build_trends([
        period("Q1", [entity("SOC-1", types=["SLOW_TRIAGE"], execution=1)]),
        period("Q2", [entity("SOC-1")]),
    ])
    assert report.repeated_findings == []


def test_a_finding_in_several_periods_is_repeated_with_its_counts():
    report = build_trends([
        period("Q1", [entity("SOC-1", types=["SLOW_TRIAGE"] * 3, execution=3)]),
        period("Q2", [entity("SOC-1", types=["SLOW_TRIAGE"] * 5, execution=5)]),
    ])
    repeated = report.repeated_findings
    assert len(repeated) == 1
    assert repeated[0].finding_type == "SLOW_TRIAGE"
    assert repeated[0].counts == [3, 5]
    assert repeated[0].period_count == 2


def test_persistent_findings_are_those_in_every_period():
    """A condition present throughout was never resolved."""
    report = build_trends([
        period("Q1", [entity("SOC-1", types=["SLOW_TRIAGE"], execution=1,
                              negative=1)]),
        period("Q2", [entity("SOC-1", types=["SLOW_TRIAGE"], execution=1)]),
        period("Q3", [entity("SOC-1", types=["SLOW_TRIAGE"], execution=1)]),
    ])
    persistent = persistent_findings(report)
    assert {p.finding_type for p in persistent} == {"SLOW_TRIAGE"}

    # TELEMETRY_GAP appeared once and was resolved; not persistent.
    assert "TELEMETRY_GAP" not in {p.finding_type for p in persistent}


def test_summary_counts_only_consistent_trends():
    report = build_trends([
        period("Q1", [entity("SOC-1", score=10.0), entity("SOC-2", score=50.0)]),
        period("Q2", [entity("SOC-1", score=20.0), entity("SOC-2", score=10.0)]),
        period("Q3", [entity("SOC-1", score=30.0), entity("SOC-2", score=50.0)]),
    ])
    # SOC-1 rises monotonically; SOC-2 wanders.
    assert report.risk_score["SOC-1"].is_trend is True
    assert report.risk_score["SOC-2"].is_trend is False
    assert "1 of 2 entities show a consistent" in report.summary()
