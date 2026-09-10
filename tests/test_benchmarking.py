"""
Peer benchmarking tests.

Benchmarking is the part of a supervisory assessment most easily
misread, so most of these tests defend two lines:

  1. A comparison the data cannot support is not made. A percentile
     drawn from a group of one is an entity compared with itself.
  2. Deviation is reported as a difference, never as a verdict. The
     wording must stay descriptive, and the numbers must be the ones
     the engine already published so a supervisor can check them.
"""

import pytest

from application.services.benchmark_service import (
    MIN_PEERS,
    RATE_NOISE_FLOOR,
    MetricComparison,
    benchmarking_caveat,
    build_peer_groups,
    build_positions,
)


def entity(soc_id, group="FINANCE", score=10.0, breakdown=()):
    return {
        "soc_id": soc_id,
        "organization_name": f"{soc_id} Ltd",
        "peer_group": group,
        "supervisory_risk_score": score,
        "score_breakdown": list(breakdown),
        "execution_gap_findings": [],
        "negative_space_findings": [],
    }


def component(finding_type, raw=0, rate=0.0, weight=1.0):
    return {"finding_type": finding_type, "raw_count": raw,
            "normalized_per_100": rate, "weight": weight,
            "contribution": rate * weight}


@pytest.fixture
def group_of_three():
    return {"entities": [
        entity("SOC-1", score=100.0, breakdown=[
            component("slow_triage", 160, 40.0),
            component("missed_escalation", 25, 6.25)]),
        entity("SOC-2", score=50.0, breakdown=[
            component("slow_triage", 47, 11.75),
            component("missed_escalation", 10, 2.5)]),
        entity("SOC-3", score=20.0, breakdown=[
            component("slow_triage", 40, 10.0),
            component("missed_escalation", 8, 2.0)]),
    ]}


# ---------------------------------------------------------------------------
# Groups too small are not benchmarked
# ---------------------------------------------------------------------------

def test_a_group_of_one_is_not_comparable():
    results = {"entities": [entity("SOC-1", group="TELECOM")]}
    position = build_positions(results)[0]

    assert position.peer_count == 1
    assert position.comparable is False
    assert "too small to benchmark" in position.position_label


def test_a_group_of_two_is_not_comparable():
    """Better-or-worse-than-one-other is not a benchmark."""
    results = {"entities": [entity("SOC-1", score=10.0),
                             entity("SOC-2", score=20.0)]}
    assert all(not p.comparable for p in build_positions(results))


def test_a_group_of_three_is_comparable(group_of_three):
    positions = build_positions(group_of_three)
    assert all(p.comparable for p in positions)
    assert all(p.peer_count == MIN_PEERS for p in positions)


def test_caveat_names_undersized_groups():
    results = {"entities": [entity("SOC-1", group="TELECOM"),
                             entity("SOC-2", group="ENERGY"),
                             entity("SOC-3", group="ENERGY")]}
    caveat = benchmarking_caveat(build_peer_groups(results))
    assert "TELECOM" in caveat
    assert "not a benchmark" in caveat
    assert "risk scores remain fully valid" in caveat


def test_caveat_when_every_group_is_large_enough(group_of_three):
    caveat = benchmarking_caveat(build_peer_groups(group_of_three))
    assert "indicator for review, not a finding" in caveat


# ---------------------------------------------------------------------------
# Comparison is within the group, and faithful to the engine's numbers
# ---------------------------------------------------------------------------

def test_peers_are_only_entities_in_the_same_group():
    """Comparing a telecom SOC with a bank says nothing useful."""
    results = {"entities": [
        entity("SOC-1", group="FINANCE", score=10.0),
        entity("SOC-2", group="FINANCE", score=20.0),
        entity("SOC-3", group="FINANCE", score=30.0),
        entity("SOC-4", group="ENERGY", score=1000.0),
        entity("SOC-5", group="ENERGY", score=1002.0),
        entity("SOC-6", group="ENERGY", score=1004.0),
    ]}
    positions = {p.soc_id: p for p in build_positions(results)}

    assert positions["SOC-2"].peer_median == 20.0
    assert positions["SOC-5"].peer_median == 1002.0


def test_an_entity_is_not_its_own_peer(group_of_three):
    """
    The peer median a finding type is compared against must exclude the
    entity itself, or an outlier drags its own baseline toward it.
    """
    position = next(p for p in build_positions(group_of_three)
                    if p.soc_id == "SOC-1")
    slow = next(c for c in position.comparisons
                if c.finding_type == "slow_triage")

    assert 40.0 not in slow.peer_rates
    assert sorted(slow.peer_rates) == [10.0, 11.75]
    assert slow.peer_median_rate == pytest.approx(10.875)


def test_rates_come_from_the_published_score_breakdown(group_of_three):
    position = next(p for p in build_positions(group_of_three)
                    if p.soc_id == "SOC-1")
    slow = next(c for c in position.comparisons
                if c.finding_type == "slow_triage")

    assert slow.entity_rate == 40.0
    assert slow.raw_count == 160


def test_notable_comparisons_are_largest_deviation_first(group_of_three):
    position = next(p for p in build_positions(group_of_three)
                    if p.soc_id == "SOC-1")
    deviations = [abs(c.deviation) for c in position.notable()]
    assert deviations == sorted(deviations, reverse=True)


def test_rank_within_group_is_worst_first(group_of_three):
    positions = {p.soc_id: p for p in build_positions(group_of_three)}
    assert positions["SOC-1"].rank_in_group == 1
    assert positions["SOC-3"].rank_in_group == 3


# ---------------------------------------------------------------------------
# Wording stays descriptive, never a verdict
# ---------------------------------------------------------------------------

def test_a_small_difference_is_reported_as_in_line():
    comparison = MetricComparison(
        finding_type="slow_triage", raw_count=10,
        entity_rate=2.00, peer_median_rate=2.00 + RATE_NOISE_FLOOR / 2,
        peer_rates=[2.0])
    assert comparison.direction == "in line"
    assert comparison.label() == "in line with peers"


def test_a_large_difference_is_expressed_as_a_multiple():
    comparison = MetricComparison(
        finding_type="slow_triage", raw_count=160,
        entity_rate=40.0, peer_median_rate=10.0, peer_rates=[10.0])
    assert comparison.direction == "above"
    assert comparison.multiple == pytest.approx(4.0)
    assert "4.0x the peer median" in comparison.label()


def test_a_zero_peer_median_yields_no_multiple():
    """Dividing by a zero baseline would report an infinite multiple."""
    comparison = MetricComparison(
        finding_type="telemetry_gap", raw_count=3,
        entity_rate=0.75, peer_median_rate=0.0, peer_rates=[0.0])
    assert comparison.multiple is None
    assert "per 100 alerts above" in comparison.label()


def test_comparison_language_states_difference_not_fault(group_of_three):
    verdicts = ("failing", "inadequate", "insecure", "worse than",
                 "poor", "deficient")
    for position in build_positions(group_of_three):
        assert not any(v in position.position_label.lower() for v in verdicts)
        for comparison in position.comparisons:
            assert not any(v in comparison.label().lower() for v in verdicts)


def test_below_median_is_reported_as_plainly_as_above(group_of_three):
    position = next(p for p in build_positions(group_of_three)
                    if p.soc_id == "SOC-3")
    assert "below the" in position.position_label


# ---------------------------------------------------------------------------
# Generator produces benchmarkable data
# ---------------------------------------------------------------------------

def test_generator_assigns_viable_peer_groups():
    """
    Sectors used to be chosen at random per entity, which left groups of
    one and two at every scale — peer benchmarking could not be shown on
    its own demo data.
    """
    import importlib.util
    import os
    from collections import Counter

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "gen", os.path.join(root, "data", "generator",
                             "generate_dataset.py"))
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    for count in (3, 5, 6, 9, 12, 20, 30):
        sizes = Counter(generator.assign_sectors(count))
        assert all(size >= MIN_PEERS for size in sizes.values()), count
        assert sum(sizes.values()) == count


def test_empty_results_are_handled():
    assert build_positions({}) == []
    assert build_peer_groups({}) == {}
    assert benchmarking_caveat({}) == ""
