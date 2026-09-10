"""
Review queue tests.

The queue's stated purpose is to correlate findings that share an alert
into one case, "so a supervisor sees compounding problems together
instead of as unrelated rows". These tests hold that the grouping is
faithful to what the engine published, that nothing is lost or
invented in reassembling it, and that the reason a case ranks where it
does is arithmetic a supervisor can redo by hand.
"""

import json
import os

import pytest
import yaml

from application.services import review_service as rs
from application.services.rule_reference import RULES, category_for, describe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "outputs", "desktop_assessment",
                       "assessment_results.json")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.exists(RESULTS),
    reason="no assessment output; run main.py first")


@pytest.fixture(scope="module")
def queue():
    with open(RESULTS) as handle:
        return json.load(handle)["review_queue"]


@pytest.fixture(scope="module")
def cases(queue):
    return rs.build_cases(queue)


def record(**overrides):
    base = {
        "queue_rank": 1, "case_rank": 1, "soc_id": "SOC-1",
        "finding_type": "SLOW_TRIAGE", "rule_id": "ACK-SLA-001",
        "severity": "HIGH", "alert_id": "ALT-1", "case_id": "CASE-1",
        "finding_weight": 1.0, "severity_boost": 1.5, "queue_priority": 1.5,
        "case_priority": 1.5, "case_finding_count": 1, "rationale": "r",
        "evidence": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Grouping is faithful — nothing lost, nothing invented
# ---------------------------------------------------------------------------

def test_every_queued_finding_lands_in_exactly_one_case(queue, cases):
    grouped = sum(case.finding_count for case in cases)
    assert grouped == len(queue)

    seen = [id(f) for case in cases for f in case.findings]
    assert len(seen) == len(set(seen)), "a finding appeared in two cases"


def test_cases_match_the_engines_own_case_ranks(queue, cases):
    """The grouping reproduces the engine's, it does not invent one."""
    assert len({r["case_rank"] for r in queue}) == len(cases)


def test_case_priority_is_the_sum_of_its_findings(cases):
    for case in cases:
        total = sum(float(f["queue_priority"]) for f in case.findings)
        assert abs(total - case.case_priority) < 0.001, case.case_rank


def test_queue_priority_is_weight_times_severity_boost(queue):
    """The arithmetic the UI shows must be the arithmetic the engine did."""
    for row in queue:
        expected = float(row["finding_weight"]) * float(row["severity_boost"])
        assert abs(expected - float(row["queue_priority"])) < 0.001, row


def test_cases_are_ordered_worst_first(cases):
    ranks = [case.case_rank for case in cases]
    assert ranks == sorted(ranks)


def test_findings_within_a_case_are_ordered_worst_first(cases):
    for case in cases:
        priorities = [float(f["queue_priority"]) for f in case.findings]
        assert priorities == sorted(priorities, reverse=True), case.case_rank


def test_compound_cases_exist_in_the_real_queue(cases):
    """
    The feature only matters if real data produces compounding cases.
    If this ever fails, the flat list would have been adequate.
    """
    compound = [c for c in cases if c.finding_count > 1]
    assert compound, "no case correlated more than one finding"
    assert max(c.finding_count for c in compound) >= 3


def test_a_compound_case_shares_one_alert(cases):
    for case in cases:
        if case.finding_count < 2:
            continue
        alerts = {f.get("alert_id") for f in case.findings}
        assert len(alerts) == 1, f"case {case.case_rank} spans {alerts}"


def test_worst_severity_is_the_worst_not_the_first():
    case = rs.build_cases([
        record(case_rank=1, severity="LOW", queue_priority=1),
        record(case_rank=1, severity="CRITICAL", queue_priority=2),
        record(case_rank=1, severity="MEDIUM", queue_priority=3),
    ])[0]
    assert case.worst_severity == "CRITICAL"


def test_records_without_a_case_rank_are_kept_not_dropped():
    """A queue item with no case must still be reviewable."""
    cases = rs.build_cases([record(case_rank=None), record(case_rank=None)])
    assert len(cases) == 2
    assert sum(c.finding_count for c in cases) == 2


def test_empty_queue_produces_no_cases():
    assert rs.build_cases([]) == []
    assert rs.queue_statistics([])["cases"] == 0


# ---------------------------------------------------------------------------
# Why prioritised — recomputable by hand
# ---------------------------------------------------------------------------

def test_why_prioritised_shows_every_finding_and_the_sum(cases):
    case = next(c for c in cases if c.finding_count > 1)
    why = case.why_prioritised()

    for finding in case.findings:
        assert finding["finding_type"] in why
    assert "CASE PRIORITY (sum)" in why
    assert f"{case.case_priority:g}" in why


def test_why_prioritised_explains_the_grouping_only_when_compound(cases):
    compound = next(c for c in cases if c.finding_count > 1)
    assert "share one alert" in compound.why_prioritised()

    single = rs.build_cases([record()])[0]
    assert "share one alert" not in single.why_prioritised()


def test_case_summary_does_not_repeat_a_finding_type():
    case = rs.build_cases([
        record(case_rank=1, finding_type="SLOW_TRIAGE", queue_priority=2),
        record(case_rank=1, finding_type="SLOW_TRIAGE", queue_priority=1),
        record(case_rank=1, finding_type="FAST_CLOSURE", queue_priority=3),
    ])[0]
    assert case.summary.count("Slow Triage") == 1
    assert "Fast Closure" in case.summary


# ---------------------------------------------------------------------------
# Statistics and filtering
# ---------------------------------------------------------------------------

def test_statistics_agree_with_the_cases(queue, cases):
    stats = rs.queue_statistics(cases)
    assert stats["cases"] == len(cases)
    assert stats["findings"] == len(queue)
    assert stats["largest_case"] == max(c.finding_count for c in cases)
    assert sum(stats["by_severity"].values()) == len(cases)


def test_filters_narrow_without_reordering(cases):
    filtered = rs.filter_cases(cases, soc_id="SOC-003")
    assert filtered, "expected SOC-003 cases"
    assert all(c.soc_id == "SOC-003" for c in filtered)
    assert [c.case_rank for c in filtered] == sorted(
        c.case_rank for c in filtered)


def test_compound_only_filter(cases):
    filtered = rs.filter_cases(cases, compound_only=True)
    assert all(c.finding_count > 1 for c in filtered)


def test_severity_filter(cases):
    filtered = rs.filter_cases(cases, severity="CRITICAL")
    assert all(c.worst_severity == "CRITICAL" for c in filtered)


def test_search_matches_rule_ids(cases):
    filtered = rs.filter_cases(cases, query="INVESTIGATION-ABSENT-001")
    assert filtered
    for case in filtered:
        assert any("INVESTIGATION-ABSENT-001" in str(f.get("rule_id"))
                   for f in case.findings)


def test_search_that_matches_nothing_returns_nothing(cases):
    assert rs.filter_cases(cases, query="zzz-no-such-thing") == []


def test_filters_never_mutate_a_case(cases):
    before = [(c.case_rank, c.finding_count) for c in cases]
    rs.filter_cases(cases, soc_id="SOC-003", severity="CRITICAL",
                     query="escalation", compound_only=True)
    assert [(c.case_rank, c.finding_count) for c in cases] == before


# ---------------------------------------------------------------------------
# Rule catalogue completeness (shared by both detail views)
# ---------------------------------------------------------------------------

def test_every_rule_has_a_supervisory_category():
    with open(CONFIG) as handle:
        config = yaml.safe_load(handle)
    for rule_id in RULES:
        assert describe(rule_id, config)["category"] in (
            "Execution Gap", "Negative Space"), rule_id


def test_category_split_matches_the_engine():
    """Nine execution-gap rules and five negative-space rules."""
    execution = [r for r in RULES if category_for(r) == "Execution Gap"]
    negative = [r for r in RULES if category_for(r) == "Negative Space"]
    assert len(execution) == 9
    assert len(negative) == 5


def test_unknown_rule_has_no_category():
    assert category_for("NO-SUCH-RULE") == ""
