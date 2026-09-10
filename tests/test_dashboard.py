"""
Dashboard computation tests.

The dashboard is a projection of the assessment, not a second analytics
layer, so these tests exist mainly to hold two lines:

  1. Its totals must agree with the assessment's own. A dashboard that
     quietly loses findings (severity buckets that drop unrated rows, a
     split that does not sum) tells a supervisor a number the engine
     never produced.
  2. It must not present a comparison the data cannot support. Peer
     percentiles from a group of one are an artefact of comparing an
     entity with itself.
"""

import json
import os

import pytest

from application.services import dashboard_service as ds
from application.services.dashboard_service import (
    ANOMALY_RULES,
    MIN_PEERS_FOR_COMPARISON,
    SEVERITY_ORDER,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "outputs", "desktop_assessment",
                       "assessment_results.json")

pytestmark = pytest.mark.skipif(
    not os.path.exists(RESULTS),
    reason="no assessment output; run main.py first")


@pytest.fixture(scope="module")
def results():
    with open(RESULTS) as handle:
        return json.load(handle)


def entity(soc_id, peer_group="FINANCE", score=10.0, rank=1,
            execution=(), negative=(), breakdown=()):
    return {
        "soc_id": soc_id,
        "organization_name": f"{soc_id} Ltd",
        "peer_group": peer_group,
        "priority_rank": rank,
        "supervisory_risk_score": score,
        "supervisory_risk_score_percentile": 50.0,
        "execution_gap_findings": list(execution),
        "negative_space_findings": list(negative),
        "score_breakdown": list(breakdown),
    }


def finding(finding_type="SLOW_TRIAGE", severity="HIGH"):
    return {"finding_type": finding_type, "severity": severity,
            "rule_id": "R-1", "rationale": "r", "evidence": {}}


# ---------------------------------------------------------------------------
# Totals must agree with the assessment
# ---------------------------------------------------------------------------

def test_overview_matches_the_assessments_own_totals(results):
    summary = ds.overview(results, results["review_queue"])
    assert summary.entities == results["run_metadata"]["entities_assessed"]
    assert summary.total_findings == results["run_metadata"]["total_findings"]
    assert summary.execution_gaps + summary.negative_space == summary.total_findings


def test_severity_distribution_accounts_for_every_finding(results):
    """
    Entity-level rules carry no alert severity. Dropping them would make
    the chart disagree with the headline count.
    """
    distribution = ds.severity_distribution(results)
    summary = ds.overview(results)
    assert distribution.total == summary.total_findings


def test_category_distribution_accounts_for_every_finding(results):
    distribution = ds.category_distribution(results)
    summary = ds.overview(results)
    assert distribution.total == summary.total_findings


def test_critical_and_high_counts_match_the_severity_chart(results):
    summary = ds.overview(results)
    distribution = ds.severity_distribution(results)
    counts = dict(zip(distribution.labels, distribution.values))
    assert counts["CRITICAL"] == summary.critical
    assert counts["HIGH"] == summary.high


def test_severity_labels_are_ordered_worst_first(results):
    labels = ds.severity_distribution(results).labels
    assert labels[:4] == list(SEVERITY_ORDER)


def test_unrated_findings_are_shown_not_dropped():
    """A telemetry gap has no alert severity; it must still be counted."""
    results = {"entities": [entity(
        "SOC-1",
        negative=[{"finding_type": "TELEMETRY_GAP", "rule_id": "T-1"}])]}
    distribution = ds.severity_distribution(results)
    assert "UNRATED" in distribution.labels
    assert distribution.total == 1


def test_anomalies_count_only_statistical_rules():
    results = {"entities": [entity(
        "SOC-1",
        execution=[finding("SLOW_TRIAGE"), finding("ANALYST_OVERLOAD")],
        negative=[finding("LOW_ACTIVITY_OUTLIER"), finding("TELEMETRY_GAP")])]}
    assert ds.overview(results).anomalies == 2
    assert ANOMALY_RULES == {"LOW_ACTIVITY_OUTLIER", "ANALYST_OVERLOAD"}


# ---------------------------------------------------------------------------
# Peer comparison honesty
# ---------------------------------------------------------------------------

def test_a_peer_group_of_one_is_not_reported_as_a_percentile():
    results = {"entities": [entity("SOC-1", peer_group="TELECOM")]}
    row = ds.entity_rankings(results)[0]

    assert row.peer_count == 1
    assert row.comparable is False
    assert row.percentile_label == "n=1"
    assert "too small" in row.deviation_label


def test_a_large_enough_peer_group_is_compared():
    results = {"entities": [
        entity(f"SOC-{i}", peer_group="FINANCE", score=float(i), rank=i)
        for i in range(1, MIN_PEERS_FOR_COMPARISON + 2)
    ]}
    rows = ds.entity_rankings(results)

    assert all(row.comparable for row in rows)
    assert any("above peer median" in r.deviation_label for r in rows)
    assert any("below peer median" in r.deviation_label for r in rows)


def test_peer_median_is_computed_within_the_group_not_across_all():
    """Comparing a telecom SOC against a bank says nothing useful."""
    results = {"entities": [
        entity("SOC-1", peer_group="FINANCE", score=10.0, rank=1),
        entity("SOC-2", peer_group="FINANCE", score=20.0, rank=2),
        entity("SOC-3", peer_group="FINANCE", score=30.0, rank=3),
        entity("SOC-4", peer_group="ENERGY", score=1000.0, rank=4),
        entity("SOC-5", peer_group="ENERGY", score=1002.0, rank=5),
        entity("SOC-6", peer_group="ENERGY", score=1004.0, rank=6),
    ]}
    rows = {row.soc_id: row for row in ds.entity_rankings(results)}
    assert rows["SOC-2"].peer_median == 20.0
    assert rows["SOC-5"].peer_median == 1002.0


def test_peer_note_names_the_undersized_groups(results):
    rows = ds.entity_rankings(results)
    note = ds.peer_comparison_note(rows)
    assert "not a benchmark" in note
    assert "risk scores remain fully valid" in note


def test_rankings_are_ordered_by_rank(results):
    ranks = [row.rank for row in ds.entity_rankings(results)]
    assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# Risk drivers — the WHY
# ---------------------------------------------------------------------------

def test_risk_drivers_sum_to_the_entity_score(results):
    """
    Auditability: the drivers shown are the score. If they did not add
    up, the "why" would not explain the "who".
    """
    for row in ds.entity_rankings(results):
        drivers = ds.risk_drivers(results, row.soc_id, limit=100)
        assert abs(sum(d.contribution for d in drivers) - row.risk_score) < 0.05


def test_risk_drivers_are_largest_first(results):
    drivers = ds.risk_drivers(results, "SOC-003")
    contributions = [d.contribution for d in drivers]
    assert contributions == sorted(contributions, reverse=True)


def test_risk_drivers_exclude_zero_contributors(results):
    drivers = ds.risk_drivers(results, "SOC-003", limit=100)
    assert all(d.contribution > 0 for d in drivers)


def test_risk_driver_shares_sum_to_one(results):
    drivers = ds.risk_drivers(results, "SOC-003", limit=100)
    assert abs(sum(d.share for d in drivers) - 1.0) < 0.001


def test_risk_drivers_for_an_unknown_entity_are_empty(results):
    assert ds.risk_drivers(results, "SOC-DOES-NOT-EXIST") == []


# ---------------------------------------------------------------------------
# Review preview — the WHAT TO REVIEW
# ---------------------------------------------------------------------------

def test_review_preview_is_the_top_of_the_queue(results):
    preview = ds.review_preview(results["review_queue"], limit=10)
    assert len(preview) == 10
    assert [r["queue_rank"] for r in preview] == sorted(
        r["queue_rank"] for r in preview)
    assert preview[0]["queue_rank"] == 1


def test_review_preview_handles_an_empty_queue():
    assert ds.review_preview([], limit=10) == []


# ---------------------------------------------------------------------------
# Trends — never fabricated
# ---------------------------------------------------------------------------

def test_a_single_period_reports_no_trend_data(results):
    status = ds.trend_status(results)
    assert status.available is False
    assert status.periods == 1
    assert "no trend data" in status.message.lower()


def test_multiple_periods_enable_trends():
    results = {"entities": [entity("SOC-1")],
               "run_metadata": {"submission_periods": 4}}
    status = ds.trend_status(results)
    assert status.available is True
    assert status.periods == 4


def test_no_assessment_reports_nothing_loaded():
    assert ds.trend_status({}).available is False


# ---------------------------------------------------------------------------
# Empty input never raises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: ds.overview({}),
    lambda: ds.entity_rankings({}),
    lambda: ds.severity_distribution({}),
    lambda: ds.category_distribution({}),
    lambda: ds.finding_type_distribution({}),
    lambda: ds.peer_comparison_note([]),
    lambda: ds.risk_drivers({}, "SOC-1"),
])
def test_empty_results_are_handled(call):
    call()
