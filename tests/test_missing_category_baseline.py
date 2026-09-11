"""
MISSING_ALERT_CATEGORY peer baseline.

"Expected" only means something against comparable entities. An energy
CSE not reporting a category every healthcare CSE reports says nothing:
the two run different technology against different threats.

Pooling sectors fails in both directions, and both are tested here —
it invents gaps that are not gaps, and dilutes real ones below the
threshold.
"""

import pandas as pd
import pytest

from analytics.detection.negative_space import (
    UNKNOWN_PEER_GROUP,
    detect_missing_categories,
)

CFG = {"negative_space": {"missing_category_peer_presence_fraction": 0.6}}


def coverage(rows):
    """(soc_id, {categories observed})."""
    return pd.DataFrame([{"soc_id": s, "categories_observed": set(c)}
                          for s, c in rows])


def flagged(result):
    if result.empty:
        return set()
    return set(zip(result["soc_id"], result["category"]))


# ----------------------------------------------------------------------
# Pooling invents gaps
# ----------------------------------------------------------------------

def test_a_category_normal_for_another_sector_is_not_a_gap():
    """
    Every healthcare entity reports MEDICAL_DEVICE. No energy entity
    does, and none should. Pooled, MEDICAL_DEVICE is present for 50% of
    everyone — and with more healthcare entities it crosses the
    threshold and flags every energy CSE for a gap that does not exist.
    """
    # Five healthcare to three energy, so MEDICAL_DEVICE reaches 5/8 =
    # 62.5% of the pooled population and crosses the 60% threshold.
    rows = [
        ("HEALTH-1", ["MALWARE", "PHISHING", "MEDICAL_DEVICE"]),
        ("HEALTH-2", ["MALWARE", "PHISHING", "MEDICAL_DEVICE"]),
        ("HEALTH-3", ["MALWARE", "PHISHING", "MEDICAL_DEVICE"]),
        ("HEALTH-4", ["MALWARE", "PHISHING", "MEDICAL_DEVICE"]),
        ("HEALTH-5", ["MALWARE", "PHISHING", "MEDICAL_DEVICE"]),
        ("ENERGY-1", ["MALWARE", "PHISHING", "SCADA"]),
        ("ENERGY-2", ["MALWARE", "PHISHING", "SCADA"]),
        ("ENERGY-3", ["MALWARE", "PHISHING", "SCADA"]),
    ]
    peers = {s: ("HEALTHCARE" if s.startswith("HEALTH") else "ENERGY")
             for s, _ in rows}

    pooled = flagged(detect_missing_categories(coverage(rows), CFG))
    per_sector = flagged(detect_missing_categories(coverage(rows), CFG, peers))

    assert any(soc.startswith("ENERGY") and cat == "MEDICAL_DEVICE"
               for soc, cat in pooled), \
        "pooling should invent this gap — it is the behaviour being fixed"
    assert not any(cat in ("MEDICAL_DEVICE", "SCADA")
                   for _, cat in per_sector), per_sector


# ----------------------------------------------------------------------
# Pooling also hides real gaps
# ----------------------------------------------------------------------

def test_a_gap_within_a_sector_is_not_diluted_away():
    """
    Every energy peer reports SCADA except ENERGY-3 — a real blind
    spot. Pooled with a large sector that never reports SCADA, its
    presence falls below the threshold and the gap disappears.
    """
    rows = [
        ("ENERGY-1", ["MALWARE", "SCADA"]),
        ("ENERGY-2", ["MALWARE", "SCADA"]),
        ("ENERGY-3", ["MALWARE"]),                 # the blind spot
        ("ENERGY-4", ["MALWARE", "SCADA"]),
        ("HEALTH-1", ["MALWARE"]), ("HEALTH-2", ["MALWARE"]),
        ("HEALTH-3", ["MALWARE"]), ("HEALTH-4", ["MALWARE"]),
        ("HEALTH-5", ["MALWARE"]), ("HEALTH-6", ["MALWARE"]),
    ]
    peers = {s: ("ENERGY" if s.startswith("ENERGY") else "HEALTHCARE")
             for s, _ in rows}

    pooled = flagged(detect_missing_categories(coverage(rows), CFG))
    per_sector = flagged(detect_missing_categories(coverage(rows), CFG, peers))

    assert ("ENERGY-3", "SCADA") not in pooled, \
        "pooling should dilute this away — the behaviour being fixed"
    assert ("ENERGY-3", "SCADA") in per_sector


# ----------------------------------------------------------------------
# When the comparison cannot be made
# ----------------------------------------------------------------------

@pytest.mark.parametrize("size", [1, 2])
def test_a_sector_below_the_peer_minimum_is_not_assessed(size):
    rows = [(f"SMALL-{i}", ["MALWARE"]) for i in range(size)]
    rows += [("BIG-1", ["MALWARE", "SCADA"]), ("BIG-2", ["MALWARE", "SCADA"]),
             ("BIG-3", ["MALWARE", "SCADA"])]
    peers = {s: ("SMALL" if s.startswith("SMALL") else "BIG") for s, _ in rows}

    result = flagged(detect_missing_categories(coverage(rows), CFG, peers))
    assert not any(soc.startswith("SMALL") for soc, _ in result)


def test_entities_without_a_sector_are_compared_only_with_each_other():
    rows = [("KNOWN-1", ["MALWARE", "SCADA"]), ("KNOWN-2", ["MALWARE", "SCADA"]),
            ("KNOWN-3", ["MALWARE", "SCADA"]),
            ("BLANK-1", ["MALWARE", "PHISHING"]),
            ("BLANK-2", ["MALWARE", "PHISHING"]),
            ("BLANK-3", ["MALWARE"])]
    peers = {s: ("ENERGY" if s.startswith("KNOWN") else None) for s, _ in rows}

    result = detect_missing_categories(coverage(rows), CFG, peers)
    assert ("BLANK-3", "PHISHING") in flagged(result)

    row = result[result["soc_id"] == "BLANK-3"].iloc[0]
    assert row["evidence"]["peer_group"] == UNKNOWN_PEER_GROUP
    # Never compared against the sector it does not belong to.
    assert ("BLANK-3", "SCADA") not in flagged(result)


def test_no_entities_at_all():
    assert detect_missing_categories(pd.DataFrame(), CFG, {}).empty


def test_a_category_no_peer_reports_is_not_expected_of_anyone():
    rows = [("A", ["MALWARE"]), ("B", ["MALWARE"]), ("C", ["MALWARE"]),
            ("D", ["MALWARE", "EXOTIC"])]
    peers = {s: "SECTOR" for s, _ in rows}
    result = flagged(detect_missing_categories(coverage(rows), CFG, peers))
    assert not any(cat == "EXOTIC" for _, cat in result)


# ----------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------

def test_evidence_names_the_peer_group_it_compared_against():
    rows = [("E-1", ["MALWARE", "SCADA"]), ("E-2", ["MALWARE", "SCADA"]),
            ("E-3", ["MALWARE"]), ("E-4", ["MALWARE", "SCADA"])]
    peers = {s: "ENERGY" for s, _ in rows}

    result = detect_missing_categories(coverage(rows), CFG, peers)
    evidence = result.iloc[0]["evidence"]

    assert evidence["peer_group"] == "ENERGY"
    assert evidence["peer_entity_count"] == 4
    assert evidence["peers_with_category"] == 3
    assert evidence["peer_presence_fraction"] == pytest.approx(0.75)
    assert evidence["peer_presence_threshold"] == 0.6


def test_the_rationale_hedges():
    rows = [("E-1", ["MALWARE", "SCADA"]), ("E-2", ["MALWARE", "SCADA"]),
            ("E-3", ["MALWARE"]), ("E-4", ["MALWARE", "SCADA"])]
    peers = {s: "ENERGY" for s, _ in rows}
    rationale = detect_missing_categories(coverage(rows), CFG, peers).iloc[0]["rationale"]

    assert "may reflect" in rationale
    assert "not a defect on its own" in rationale
    assert "ENERGY" in rationale


def test_findings_are_deterministic():
    rows = [("E-1", ["MALWARE", "SCADA", "PHISHING"]),
            ("E-2", ["MALWARE", "SCADA", "PHISHING"]),
            ("E-3", ["MALWARE"]),
            ("E-4", ["MALWARE", "SCADA", "PHISHING"])]
    peers = {s: "ENERGY" for s, _ in rows}
    frame = coverage(rows)

    first = detect_missing_categories(frame, CFG, peers)
    for _ in range(3):
        again = detect_missing_categories(frame, CFG, peers)
        assert again["category"].tolist() == first["category"].tolist()
        assert again["soc_id"].tolist() == first["soc_id"].tolist()
