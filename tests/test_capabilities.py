"""
Capability mapping tests.

The problem statement frames the assessment as eight supervisory
capabilities. This maps findings onto them, and the tests defend three
properties:

  1. It is a lookup, not an inference. The mapping lives in
     configuration, every rule resolves through it, and an unmapped rule
     is visibly unmapped rather than filed under a guess.
  2. It does not double-count. A finding informs one capability for
     scoring purposes, so per-capability contributions still sum to the
     entity's risk score and stay checkable by hand.
  3. It does not overstate reach. "No findings" and "no rule assesses
     this" are different answers, and conflating them would tell a
     supervisor a capability was examined when it was not.
"""

import os

import pytest
import yaml

from application.services import capability_service as cs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")

#: Every rule the engine can emit, as finding_type.
EMITTED_RULES = {
    "MISSED_ESCALATION", "SLOW_TRIAGE", "FAST_CLOSURE", "MISSING_EVIDENCE",
    "REOPENED_CASE", "REPETITIVE_INVESTIGATION", "ANALYST_OVERLOAD",
    "ACK_WITHOUT_INVESTIGATION", "REPEATED_ALERT_WITHOUT_REMEDIATION",
    "TELEMETRY_GAP", "MISSING_ALERT_CATEGORY", "LOW_ACTIVITY_OUTLIER",
    "MISSING_ESCALATION_RECORDS", "MISSING_INVESTIGATIONS",
}

#: The eight areas the official problem statement names.
PS_AREAS = {
    "THREAT_DETECTION", "INVESTIGATION", "ESCALATION", "INCIDENT_RESPONSE",
    "SECURITY_OPERATIONS", "GOVERNANCE_OVERSIGHT", "OPERATIONAL_DISCIPLINE",
    "CYBER_RESILIENCE",
}


@pytest.fixture(scope="module")
def config():
    with open(CONFIG) as handle:
        return yaml.safe_load(handle)


# ---------------------------------------------------------------------------
# A lookup, not an inference
# ---------------------------------------------------------------------------

def test_the_eight_problem_statement_areas_are_present(config):
    assert set(cs.load_areas(config)) == PS_AREAS


def test_every_area_states_the_supervisory_question_it_answers(config):
    for area in cs.load_areas(config).values():
        assert area.label
        assert area.question.endswith("?"), area.key


def test_every_emitted_rule_is_mapped(config):
    """A rule shipped without a mapping would silently vanish."""
    mapped = set(cs.load_rule_mappings(config))
    assert EMITTED_RULES - mapped == set()


def test_no_mapping_exists_for_a_rule_the_engine_cannot_emit(config):
    """A stale mapping is as misleading as a missing one."""
    mapped = set(cs.load_rule_mappings(config))
    assert mapped - EMITTED_RULES == set()


def test_every_primary_area_is_a_real_area(config):
    areas = set(cs.load_areas(config))
    for finding_type, mapping in cs.load_rule_mappings(config).items():
        assert mapping.primary in areas, finding_type
        for secondary in mapping.secondary:
            assert secondary in areas, f"{finding_type} -> {secondary}"


def test_a_rule_is_never_its_own_secondary(config):
    for finding_type, mapping in cs.load_rule_mappings(config).items():
        assert mapping.primary not in mapping.secondary, finding_type


def test_every_mapping_explains_itself(config):
    """
    A supervisor must be able to disagree with an assignment on its
    merits rather than guessing at the intent.
    """
    for finding_type, mapping in cs.load_rule_mappings(config).items():
        assert len(mapping.why) > 40, finding_type


def test_an_unmapped_rule_returns_nothing_rather_than_a_guess(config):
    assert cs.capability_for("SOME_NEW_RULE", config) is None
    assert cs.capability_label("SOME_NEW_RULE", config) == ""


def test_mapping_is_deterministic(config):
    """Same input, same answer, every time — it is a table lookup."""
    for _ in range(3):
        assert cs.capability_label("ACK_WITHOUT_INVESTIGATION", config) == \
            "Operational Discipline"


def test_no_model_is_involved():
    """
    The capability layer must not reach the narration backends. This
    asserts the module's imports, which is what would have to change for
    that to happen.
    """
    import inspect
    source = inspect.getsource(cs)
    for forbidden in ("narration", "llama", "ollama", "backend", "explain("):
        assert forbidden not in source.lower(), forbidden


# ---------------------------------------------------------------------------
# No double counting
# ---------------------------------------------------------------------------

def entity(soc_id="SOC-1", breakdown=()):
    return {"soc_id": soc_id, "supervisory_risk_score":
            sum(c["contribution"] for c in breakdown),
            "score_breakdown": list(breakdown)}


def component(finding_type, raw, contribution):
    return {"finding_type": finding_type, "raw_count": raw,
            "normalized_per_100": float(raw), "weight": 1.0,
            "contribution": contribution}


def test_each_rule_has_exactly_one_primary_area(config):
    for finding_type, mapping in cs.load_rule_mappings(config).items():
        assert isinstance(mapping.primary, str) and mapping.primary


def test_capability_contributions_sum_to_the_entity_score(config):
    """
    The arithmetic must still close. If a finding counted toward two
    capabilities the totals would exceed the score it came from.
    """
    subject = entity(breakdown=[
        component("SLOW_TRIAGE", 300, 37.5),
        component("MISSED_ESCALATION", 47, 17.6),
        component("TELEMETRY_GAP", 3, 1.1),
    ])
    results = cs.assess_entity(subject, config)
    total = sum(r.contribution for r in results)
    assert total == pytest.approx(subject["supervisory_risk_score"])


def test_finding_counts_are_not_duplicated_across_areas(config):
    subject = entity(breakdown=[component("SLOW_TRIAGE", 300, 37.5)])
    results = cs.assess_entity(subject, config)
    assert sum(r.finding_count for r in results) == 300

    with_findings = [r for r in results if r.finding_count]
    assert len(with_findings) == 1
    assert with_findings[0].area.key == "SECURITY_OPERATIONS"


def test_secondary_areas_are_not_scored(config):
    """
    ACK_WITHOUT_INVESTIGATION also informs Investigation, but only
    Operational Discipline is credited with it.
    """
    subject = entity(breakdown=[
        component("ACK_WITHOUT_INVESTIGATION", 22, 5.5)])
    results = {r.area.key: r for r in cs.assess_entity(subject, config)}

    assert results["OPERATIONAL_DISCIPLINE"].finding_count == 22
    assert results["INVESTIGATION"].finding_count == 0


def test_every_area_appears_for_every_entity(config):
    """An area with nothing to report still has to be reported."""
    results = cs.assess_entity(entity(breakdown=[]), config)
    assert {r.area.key for r in results} == PS_AREAS


# ---------------------------------------------------------------------------
# Does not overstate reach
# ---------------------------------------------------------------------------

def test_no_findings_is_distinct_from_not_assessed(config):
    subject = entity(breakdown=[component("SLOW_TRIAGE", 10, 1.0)])
    results = {r.area.key: r for r in cs.assess_entity(subject, config)}

    quiet = results["THREAT_DETECTION"]
    assert quiet.finding_count == 0
    assert quiet.assessed is True
    assert quiet.status == "no findings"
    assert "No findings from" in quiet.summary()


def test_an_area_with_no_rules_reports_itself_as_not_assessed():
    """
    Constructed rather than taken from the shipped config, which covers
    all eight. If a future edit leaves an area uncovered, the tool must
    say so instead of showing it as clean.
    """
    config = {"capability_mapping": {
        "areas": {"THREAT_DETECTION": {"label": "Threat Detection",
                                        "question": "Can it see?"},
                   "CYBER_RESILIENCE": {"label": "Cyber Resilience",
                                         "question": "Would it hold?"}},
        "rules": {"TELEMETRY_GAP": {"primary": "THREAT_DETECTION",
                                     "secondary": [], "why": "x"}},
    }}
    results = {r.area.key: r for r in cs.assess_entity(entity(), config)}

    assert results["CYBER_RESILIENCE"].assessed is False
    assert results["CYBER_RESILIENCE"].status == "not assessed"
    assert "No rule" in results["CYBER_RESILIENCE"].summary()

    report = cs.coverage(config)
    assert report["unassessed"] == ["CYBER_RESILIENCE"]
    assert "Not assessed: Cyber Resilience" in cs.coverage_statement(config)


def test_shipped_config_covers_every_area(config):
    report = cs.coverage(config)
    assert report["unassessed"] == []
    assert report["unmapped_rules"] == []
    assert len(report["assessed"]) == 8


def test_coverage_statement_does_not_claim_verification(config):
    """
    An area with no findings means no rule fired — not that the
    capability was independently confirmed to be sound.
    """
    statement = cs.coverage_statement(config)
    assert "not asserted" in statement
    assert "no rule fired" in statement


def test_missing_capability_mapping_is_handled():
    assert cs.load_areas({}) == {}
    assert cs.load_rule_mappings({}) == {}
    assert cs.assess_entity(entity(), {}) == []
