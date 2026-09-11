"""
Review-queue CSE coverage.

The queue is the supervisor's worklist. A purely global top-N lets a
few high-volume entities own every top case: measured on a 30-entity
submission, 21 entities received no queue item at all, one of them
ranked 3rd by risk score with 202 CRITICAL findings. A supervisor
responsible for 30 CSEs would never have looked at 21 of them.

These tests hold the two halves of the fix together. Coverage must be
real — every entity with something worth reviewing is represented — and
it must not become padding: an entity with nothing to review stays out,
and nothing is promoted above a case that outranks it.
"""

import pandas as pd
import pytest

from analytics.review_queue import build_review_queue

CONFIG = {
    "scoring": {"weights": {"slow_triage": 1.0, "missed_escalation": 3.0,
                             "telemetry_gap": 3.0}},
    "review_queue": {
        "top_n": 3,
        "per_entity_cases": 1,
        "severity_boost": {"CRITICAL": 2.0, "HIGH": 1.5, "MEDIUM": 1.0,
                            "LOW": 0.5},
    },
}


def config(**review_overrides):
    cfg = {"scoring": dict(CONFIG["scoring"]),
           "review_queue": dict(CONFIG["review_queue"])}
    cfg["review_queue"].update(review_overrides)
    return cfg


def gaps(rows):
    """Execution-gap findings: (soc_id, alert_id, finding_type, severity)."""
    return pd.DataFrame([
        {"soc_id": soc, "alert_id": alert, "case_id": f"CASE-{alert}",
         "finding_type": ftype, "rule_id": "R-1", "severity": sev,
         "assigned_analyst_id": "ANL-1", "rationale": "r", "evidence": {}}
        for soc, alert, ftype, sev in rows
    ])


def loud_entity(soc, alerts, ftype="missed_escalation", sev="CRITICAL"):
    """An entity generating many high-priority cases."""
    return [(soc, f"ALT-{soc}-{i}", ftype.upper(), sev) for i in range(alerts)]


# ----------------------------------------------------------------------
# The defect this fixes
# ----------------------------------------------------------------------

def test_a_loud_entity_cannot_starve_the_others():
    """
    Two entities produce enough CRITICAL cases to fill the global top-N
    on their own. Three quieter entities still have real findings and
    must not vanish from the supervisor's worklist.
    """
    rows = (loud_entity("SOC-LOUD-1", 10) + loud_entity("SOC-LOUD-2", 10)
            + [("SOC-QUIET-1", "ALT-Q1", "SLOW_TRIAGE", "HIGH"),
               ("SOC-QUIET-2", "ALT-Q2", "SLOW_TRIAGE", "HIGH"),
               ("SOC-QUIET-3", "ALT-Q3", "SLOW_TRIAGE", "MEDIUM")])

    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())
    represented = set(queue["soc_id"])

    assert represented == {"SOC-LOUD-1", "SOC-LOUD-2", "SOC-QUIET-1",
                            "SOC-QUIET-2", "SOC-QUIET-3"}, \
        "a quiet entity with real findings was starved out of the queue"


def test_every_entity_with_findings_is_represented_at_scale():
    """Twenty-five entities, wildly uneven volumes, none silently dropped."""
    rows = []
    for index in range(25):
        soc = f"SOC-{index:03d}"
        rows += loud_entity(soc, 1 + index * 2)

    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())
    assert len(set(queue["soc_id"])) == 25


def test_purely_global_selection_is_still_available():
    """per_entity_cases = 0 restores the previous behaviour exactly."""
    rows = loud_entity("SOC-LOUD", 10) + [
        ("SOC-QUIET", "ALT-Q", "SLOW_TRIAGE", "LOW")]

    covered = build_review_queue(gaps(rows), pd.DataFrame(), config())
    global_only = build_review_queue(gaps(rows), pd.DataFrame(),
                                      config(per_entity_cases=0))

    assert "SOC-QUIET" in set(covered["soc_id"])
    assert "SOC-QUIET" not in set(global_only["soc_id"])
    assert set(global_only["selection_reason"]) == {"global_priority"}


# ----------------------------------------------------------------------
# Coverage is not padding
# ----------------------------------------------------------------------

def test_an_entity_with_no_findings_is_not_forced_in():
    """
    A clean entity is absent from the queue, and that absence is the
    correct supervisory statement about it. Coverage must never invent
    a row to make a portfolio look uniformly examined.
    """
    rows = loud_entity("SOC-A", 5)
    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())
    assert set(queue["soc_id"]) == {"SOC-A"}


def test_coverage_adds_each_entitys_worst_case_not_its_easiest():
    rows = [("SOC-X", "ALT-X1", "SLOW_TRIAGE", "LOW"),
            ("SOC-X", "ALT-X2", "MISSED_ESCALATION", "CRITICAL"),
            ("SOC-X", "ALT-X3", "SLOW_TRIAGE", "MEDIUM")]
    rows += loud_entity("SOC-BUSY", 10)

    queue = build_review_queue(gaps(rows), pd.DataFrame(),
                               config(top_n=3, per_entity_cases=1))
    covered = queue[queue["soc_id"] == "SOC-X"]

    assert set(covered["alert_id"]) == {"ALT-X2"}, \
        "coverage must surface the entity's highest-priority case"


def test_per_entity_cases_controls_how_many_each_entity_contributes():
    rows = [("SOC-X", f"ALT-X{i}", "MISSED_ESCALATION", "CRITICAL")
            for i in range(5)]
    rows += loud_entity("SOC-BUSY", 10)

    for allowance in (1, 2, 3):
        queue = build_review_queue(gaps(rows), pd.DataFrame(),
                                    config(top_n=0,
                                           per_entity_cases=allowance))
        for soc in ("SOC-X", "SOC-BUSY"):
            cases = queue[queue["soc_id"] == soc]["case_rank"].nunique()
            assert cases == allowance, (soc, allowance, cases)


# ----------------------------------------------------------------------
# Nothing about ranking or evidence changes
# ----------------------------------------------------------------------

def test_coverage_never_promotes_a_case_above_one_that_outranks_it():
    """
    Covered cases keep the global rank they earned. Ordering stays
    case_priority, descending — a covered case appears where its own
    priority puts it, not at the top because its entity was missing.
    """
    rows = loud_entity("SOC-LOUD", 8) + [
        ("SOC-QUIET", "ALT-Q", "SLOW_TRIAGE", "LOW")]
    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())

    priorities = queue["case_priority"].tolist()
    assert priorities == sorted(priorities, reverse=True)

    quiet = queue[queue["soc_id"] == "SOC-QUIET"]
    loud_ranks = queue[queue["soc_id"] == "SOC-LOUD"]["case_rank"]
    assert quiet["case_rank"].min() > loud_ranks.max(), \
        "a covered case outranked cases with higher priority"


def test_selection_reason_says_why_each_case_is_present():
    rows = loud_entity("SOC-LOUD", 8) + [
        ("SOC-QUIET", "ALT-Q", "SLOW_TRIAGE", "LOW")]
    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())

    reasons = dict(zip(queue["soc_id"], queue["selection_reason"]))
    assert reasons["SOC-LOUD"] == "global_priority"
    assert reasons["SOC-QUIET"] == "entity_coverage"
    assert set(queue["selection_reason"]) <= {"global_priority",
                                               "entity_coverage"}


def test_a_case_in_both_sets_is_labelled_global():
    """
    The top entity's worst case is also the global worst. It is one
    case, reported once, labelled by the stronger reason.
    """
    rows = loud_entity("SOC-ONLY", 5)
    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())
    assert set(queue["selection_reason"]) == {"global_priority"}
    assert queue["case_rank"].nunique() == 3   # top_n, not 5


def test_queue_priority_arithmetic_is_untouched():
    rows = [("SOC-A", "ALT-1", "MISSED_ESCALATION", "CRITICAL")]
    queue = build_review_queue(gaps(rows), pd.DataFrame(), config())
    row = queue.iloc[0]
    assert row["finding_weight"] == 3.0
    assert row["severity_boost"] == 2.0
    assert row["queue_priority"] == 6.0
    assert row["case_priority"] == 6.0


def test_evidence_and_rationale_survive_selection():
    rows = [("SOC-A", "ALT-1", "MISSED_ESCALATION", "CRITICAL"),
            ("SOC-B", "ALT-2", "SLOW_TRIAGE", "HIGH")]
    findings = gaps(rows)
    # .at, not .loc: .loc unwraps a single-element list into the cell.
    findings.at[0, "evidence"] = {"escalation_required": True}
    findings.at[0, "rationale"] = "specific text"

    queue = build_review_queue(findings, pd.DataFrame(), config())
    first = queue[queue["alert_id"] == "ALT-1"].iloc[0]
    assert first["rationale"] == "specific text"
    assert first["evidence"] == {"escalation_required": True}


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------

def test_equal_priority_cases_order_reproducibly():
    """
    Ties are common — priorities are small discrete numbers. Ordering
    them by an unstable sort would let the queue change between runs,
    which a tool whose output is meant to be re-derivable cannot do.
    """
    rows = [(f"SOC-{i}", f"ALT-{i}", "SLOW_TRIAGE", "HIGH")
            for i in range(12)]
    findings = gaps(rows)

    first = build_review_queue(findings, pd.DataFrame(), config(top_n=5))
    for _ in range(4):
        again = build_review_queue(findings.sample(frac=1, random_state=7),
                                    pd.DataFrame(), config(top_n=5))
        assert again["case_rank"].tolist() == first["case_rank"].tolist()
        assert again["alert_id"].tolist() == first["alert_id"].tolist()


def test_empty_input_still_declares_the_selection_column():
    queue = build_review_queue(pd.DataFrame(), pd.DataFrame(), config())
    assert queue.empty
    assert "selection_reason" in queue.columns
