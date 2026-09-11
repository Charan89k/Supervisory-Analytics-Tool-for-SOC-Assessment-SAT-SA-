"""
LOW_ACTIVITY_OUTLIER peer baseline.

The rule asks whether an entity is seeing less than it should. That
question only means something against comparable entities. Sectors
differ in natural alert volume by an order of magnitude, and pooling
them inflates the standard deviation until nothing sits more than 1.5
sigma from a mean describing no real population — so genuine blind
spots are MASKED rather than over-reported.

The first test is the demonstrated case that motivated the fix.
"""

import pandas as pd
import pytest
import yaml

from analytics.detection.negative_space import (
    UNKNOWN_PEER_GROUP,
    detect_low_activity_outliers,
    peer_group_map,
)

CFG = {
    "negative_space": {"low_activity_zscore_threshold": -1.5},
    "benchmarking": {"peer_group_by": "sector"},
}

#: A healthcare sector with enough peers that the outlier does not
#: dominate its own baseline, alongside a high-volume energy sector.
MIXED = [("ENERGY-1", 9000), ("ENERGY-2", 9500), ("ENERGY-3", 8800),
         ("ENERGY-4", 9200), ("HEALTH-1", 900), ("HEALTH-2", 950),
         ("HEALTH-3", 880), ("HEALTH-4", 920), ("HEALTH-5", 890),
         ("HEALTH-6", 910), ("HEALTH-7", 150)]


def volumes(rows):
    return pd.DataFrame([{"soc_id": s, "alert_count": c} for s, c in rows])


def sectors(rows):
    return {s: ("ENERGY" if s.startswith("ENERGY") else "HEALTHCARE")
            for s, _ in rows}


def flagged(result):
    return [] if result.empty else result["soc_id"].tolist()


# ----------------------------------------------------------------------
# The defect this fixes
# ----------------------------------------------------------------------

def test_pooling_sectors_masks_a_genuine_blind_spot():
    """
    HEALTH-7 reports 150 alerts where its sector peers average 900. It
    is exactly the under-instrumented entity this rule exists to find.

    Pooled with a high-volume sector it scores -0.87 and is invisible.
    Against its own sector it scores -2.26 and is flagged.
    """
    data = volumes(MIXED)

    pooled = detect_low_activity_outliers(data, CFG)
    per_sector = detect_low_activity_outliers(data, CFG, sectors(MIXED))

    assert flagged(pooled) == [], \
        "pooling sectors should hide it — this is the behaviour being fixed"
    assert flagged(per_sector) == ["HEALTH-7"]

    evidence = per_sector.iloc[0]["evidence"]
    assert evidence["peer_group"] == "HEALTHCARE"
    assert evidence["volume_zscore"] <= -1.5
    assert evidence["peer_group_size"] == 7


def test_a_high_volume_sector_is_judged_on_its_own_scale():
    """
    An energy entity well below ITS peers is flagged even though its
    absolute volume dwarfs every healthcare entity. Absolute counts
    across sectors say nothing.
    """
    rows = [("ENERGY-1", 9000), ("ENERGY-2", 9500), ("ENERGY-3", 8800),
            ("ENERGY-4", 9200), ("ENERGY-5", 9100), ("ENERGY-6", 2000),
            ("HEALTH-1", 900), ("HEALTH-2", 950), ("HEALTH-3", 880)]
    result = detect_low_activity_outliers(volumes(rows), CFG, sectors(rows))

    assert "ENERGY-6" in flagged(result)
    assert result.iloc[0]["evidence"]["alert_count"] == 2000


# ----------------------------------------------------------------------
# When the comparison cannot be made
# ----------------------------------------------------------------------

@pytest.mark.parametrize("size", [1, 2])
def test_a_sector_below_the_peer_minimum_is_not_assessed(size):
    """
    A standard deviation over one or two entities describes nothing.
    Reporting a z-score against it would manufacture a finding out of
    an accident of who submitted.
    """
    rows = [(f"SOLO-{i}", 10 if i else 5000) for i in range(size)]
    rows += [("BIG-1", 9000), ("BIG-2", 9500), ("BIG-3", 8800)]
    peers = {s: ("SOLO" if s.startswith("SOLO") else "BIG") for s, _ in rows}

    result = detect_low_activity_outliers(volumes(rows), CFG, peers)
    assert not any(s.startswith("SOLO") for s in flagged(result))


def test_identical_volumes_produce_no_finding():
    rows = [("A", 500), ("B", 500), ("C", 500), ("D", 500)]
    peers = {s: "SECTOR" for s, _ in rows}
    assert flagged(detect_low_activity_outliers(volumes(rows), CFG, peers)) == []


def test_normal_sector_activity_is_not_flagged():
    rows = [("A", 880), ("B", 900), ("C", 920), ("D", 910), ("E", 890)]
    peers = {s: "HEALTHCARE" for s, _ in rows}
    assert flagged(detect_low_activity_outliers(volumes(rows), CFG, peers)) == []


def test_no_entities_at_all():
    assert detect_low_activity_outliers(pd.DataFrame(), CFG, {}).empty


# ----------------------------------------------------------------------
# Missing or unknown sector metadata
# ----------------------------------------------------------------------

def test_entities_without_a_sector_are_compared_only_with_each_other():
    """
    An entity with no declared sector is never folded into a named one.
    Doing so would compare it against a population it may not belong to.
    """
    rows = [("KNOWN-1", 9000), ("KNOWN-2", 9500), ("KNOWN-3", 8800),
            ("BLANK-1", 900), ("BLANK-2", 950), ("BLANK-3", 880),
            ("BLANK-4", 920), ("BLANK-5", 890), ("BLANK-6", 910),
            ("BLANK-7", 150)]
    peers = {s: ("ENERGY" if s.startswith("KNOWN") else None)
             for s, _ in rows}

    result = detect_low_activity_outliers(volumes(rows), CFG, peers)
    assert flagged(result) == ["BLANK-7"]
    assert result.iloc[0]["evidence"]["peer_group"] == UNKNOWN_PEER_GROUP


def test_rationale_does_not_claim_a_sector_that_was_never_supplied():
    rows = [("A", 900), ("B", 950), ("C", 880), ("D", 920),
            ("E", 890), ("F", 910), ("G", 150)]
    result = detect_low_activity_outliers(volumes(rows), CFG, None)
    assert flagged(result) == ["G"]
    rationale = result.iloc[0]["rationale"]
    assert "its peer group" in rationale
    assert UNKNOWN_PEER_GROUP not in rationale


# ----------------------------------------------------------------------
# Peer groups come from the shared definition, not a local one
# ----------------------------------------------------------------------

def test_peer_groups_reuse_the_benchmarking_definition():
    socs = pd.DataFrame([{"soc_id": "S1", "sector": "ENERGY"},
                          {"soc_id": "S2", "sector": "HEALTHCARE"}])
    assert peer_group_map({"socs": socs}, CFG) == {"S1": "ENERGY",
                                                    "S2": "HEALTHCARE"}


def test_peer_group_all_collapses_to_one_group():
    """`peer_group_by: all` is a legitimate setting for a small pilot."""
    socs = pd.DataFrame([{"soc_id": "S1", "sector": "ENERGY"},
                          {"soc_id": "S2", "sector": "HEALTHCARE"}])
    cfg = {**CFG, "benchmarking": {"peer_group_by": "all"}}
    assert set(peer_group_map({"socs": socs}, cfg).values()) == {"all"}


def test_a_submission_with_no_sector_column_yields_no_peer_information():
    """Reported as absent, never as 'everyone is a peer'."""
    socs = pd.DataFrame([{"soc_id": "S1"}, {"soc_id": "S2"}])
    assert peer_group_map({"socs": socs}, CFG) == {}
    assert peer_group_map({}, CFG) == {}
    assert peer_group_map({"socs": pd.DataFrame()}, CFG) == {}


def test_threshold_is_read_from_configuration():
    rows = [("A", 900), ("B", 950), ("C", 880), ("D", 920),
            ("E", 890), ("F", 910), ("G", 600)]
    peers = {s: "SECTOR" for s, _ in rows}

    strict = {**CFG, "negative_space": {"low_activity_zscore_threshold": -3.0}}
    loose = {**CFG, "negative_space": {"low_activity_zscore_threshold": -1.0}}

    assert flagged(detect_low_activity_outliers(volumes(rows), strict, peers)) == []
    assert flagged(detect_low_activity_outliers(volumes(rows), loose, peers)) == ["G"]
