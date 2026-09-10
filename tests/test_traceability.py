"""
Traceability tests.

The chain a supervisory finding has to support is:

    Finding -> Rule -> Rationale -> Evidence -> Source records

These tests hold each link, and hold the boundary that keeps the chain
trustworthy: the UI may present the record, but must never restate,
summarise, or re-derive it, and an optional AI explanation must never
become part of it.
"""

import json
import os
import shutil

import pytest
import yaml

from analytics.evidence import evidence_bundle_for_finding
from analytics.loader import load_soc_dataset
from analytics.normalizer import build_alerts_enriched, normalize_dataset
from application.services.evidence_service import EvidenceService, SourceUnavailable
from application.services.rule_reference import RULES, describe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")
RESULTS = os.path.join(ROOT, "outputs", "desktop_assessment",
                       "assessment_results.json")

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(DATA) and os.path.exists(RESULTS)),
    reason="synthetic dataset or assessment output missing")


@pytest.fixture(scope="module")
def config():
    with open(CONFIG) as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def results():
    with open(RESULTS) as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def source():
    data = normalize_dataset(load_soc_dataset(DATA))
    return data, build_alerts_enriched(data)


@pytest.fixture(scope="module")
def one_of_each(results):
    """One finding per finding_type, so every rule is exercised."""
    seen = {}
    for entity in results["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            seen.setdefault(finding["finding_type"], finding)
    return seen


# ---------------------------------------------------------------------------
# Finding -> Rule
# ---------------------------------------------------------------------------

def test_every_emitted_rule_has_a_description(one_of_each, config):
    """
    A rule_id alone is a token. An examiner asking what
    ESC-REQUIRED-001 tests must not have to read the source.
    """
    undescribed = [f["rule_id"] for f in one_of_each.values()
                   if not describe(f["rule_id"], config)]
    assert not undescribed, f"rules with no description: {undescribed}"


def test_rule_descriptions_resolve_their_configured_thresholds(config):
    """
    Thresholds are read from the live configuration, so the page cannot
    drift from the values the run actually used. A key that stops
    resolving is a silent drift, so it fails here.
    """
    for rule_id, entry in RULES.items():
        described = describe(rule_id, config)
        assert len(described["thresholds"]) == len(entry["config"]), rule_id


def test_unknown_rule_is_not_given_an_invented_description(config):
    assert describe("NO-SUCH-RULE-999", config) == {}


def test_rule_reference_finding_types_match_the_engine(one_of_each, config):
    for finding in one_of_each.values():
        described = describe(finding["rule_id"], config)
        assert described["finding_type"] == finding["finding_type"]


# ---------------------------------------------------------------------------
# Finding -> Rationale -> Evidence
# ---------------------------------------------------------------------------

def test_every_finding_carries_a_rationale_and_evidence(results):
    for entity in results["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            assert finding.get("rationale"), finding
            assert "evidence" in finding, finding


#: Language that marks a statement as an indicator rather than a verdict.
HEDGES = ("may ", "potential", "possible", "indicator", "suggest",
          "cannot be determined", "is not proof", "not itself proof",
          "not a determination", "not a finding of", "can also")


def test_every_rationale_hedges(one_of_each):
    """
    Supervisory judgement stays with the reviewer, so a rationale states
    an indicator rather than a verdict.

    Tested as "must contain a hedge" rather than "must avoid a word
    list": REPETITIVE_INVESTIGATION's rationale ends "not a finding of
    misconduct on its own", which a naive blocklist reads as accusatory
    when it is the opposite. Presence of hedging is the property that
    actually matters; absence of a keyword is not.
    """
    unhedged = [
        finding["finding_type"] for finding in one_of_each.values()
        if not any(h in finding["rationale"].lower() for h in HEDGES)
    ]
    assert not unhedged, f"rationales stated as fact: {unhedged}"


# ---------------------------------------------------------------------------
# Evidence -> Source records
# ---------------------------------------------------------------------------

def test_every_finding_type_resolves_to_source_records(one_of_each, source):
    """
    Findings are not all at the same grain. An alert rule points at an
    alert; ANALYST_OVERLOAD at an analyst; TELEMETRY_GAP at a source.
    Every one must reach real rows.
    """
    data, alerts_enriched = source
    for finding_type, finding in one_of_each.items():
        bundle = evidence_bundle_for_finding(data, alerts_enriched, finding)
        assert bundle.get("_grain"), finding_type
        payload = {k: v for k, v in bundle.items() if k != "_grain"}
        assert payload, f"{finding_type} produced an empty bundle"


def test_alert_grain_bundle_returns_the_actual_alert(one_of_each, source):
    data, alerts_enriched = source
    finding = one_of_each["MISSED_ESCALATION"]
    bundle = evidence_bundle_for_finding(data, alerts_enriched, finding)

    assert bundle["_grain"] == "alert"
    assert bundle["alert"]["alert_id"] == finding["alert_id"]
    assert bundle["lifecycle_events"], "no event trail returned"


def test_analyst_grain_bundle_is_labelled_a_sample(one_of_each, source):
    """
    An overloaded analyst may have handled hundreds of alerts. The count
    is exact; the rows are a sample and must say so in the field name.
    """
    data, alerts_enriched = source
    bundle = evidence_bundle_for_finding(
        data, alerts_enriched, one_of_each["ANALYST_OVERLOAD"])

    assert bundle["_grain"] == "analyst"
    assert bundle["alerts_handled_total"] > len(bundle["alerts_handled_sample"])
    assert "sample" in "".join(bundle)


def test_asset_grain_bundle_returns_the_recurrence(one_of_each, source):
    data, alerts_enriched = source
    finding = one_of_each["REPEATED_ALERT_WITHOUT_REMEDIATION"]
    bundle = evidence_bundle_for_finding(data, alerts_enriched, finding)

    assert bundle["_grain"] == "asset"
    assert len(bundle["asset_alerts"]) == finding["evidence"]["alert_count"]


def test_telemetry_grain_bundle_returns_the_source_row(one_of_each, source):
    data, alerts_enriched = source
    bundle = evidence_bundle_for_finding(
        data, alerts_enriched, one_of_each["TELEMETRY_GAP"])
    assert bundle["_grain"] == "telemetry source"
    assert bundle["telemetry"]


def test_source_records_are_returned_verbatim(one_of_each, source):
    """
    The drill-down must not reinterpret. The alert row returned has to
    be the row in the submission, field for field.
    """
    data, alerts_enriched = source
    finding = one_of_each["MISSED_ESCALATION"]
    bundle = evidence_bundle_for_finding(data, alerts_enriched, finding)

    raw = data["alerts"]
    original = raw[raw["alert_id"] == finding["alert_id"]].iloc[0]
    returned = bundle["alert"]

    for column in ("alert_id", "soc_id", "severity", "asset_id", "status"):
        assert str(returned[column]) == str(original[column]), column


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

def test_service_serves_bundles_and_caches_the_submission(one_of_each):
    service = EvidenceService()
    assert service.loaded_path is None

    finding = one_of_each["FAST_CLOSURE"]
    bundle = service.bundle_for(finding, DATA)

    assert bundle["alert"]["alert_id"] == finding["alert_id"]
    assert service.is_loaded_for(DATA)

    # Second call must not reload.
    before = service._data
    service.bundle_for(one_of_each["SLOW_TRIAGE"], DATA)
    assert service._data is before


def test_missing_submission_reports_unavailable_not_empty(one_of_each):
    """
    A supervisor who cannot reach the source must be told the submission
    moved — not shown a blank panel that reads as "no evidence existed".
    """
    service = EvidenceService()
    with pytest.raises(SourceUnavailable) as excinfo:
        service.bundle_for(one_of_each["FAST_CLOSURE"], "/no/such/dataset")
    assert "could not be re-read" in str(excinfo.value)


def test_no_dataset_path_is_reported_clearly(one_of_each):
    service = EvidenceService()
    with pytest.raises(SourceUnavailable, match="No dataset"):
        service.bundle_for(one_of_each["FAST_CLOSURE"], "")


def test_reset_drops_the_cache(one_of_each):
    """A new assessment may use a different submission."""
    service = EvidenceService()
    service.bundle_for(one_of_each["FAST_CLOSURE"], DATA)
    assert service.loaded_path == DATA

    service.reset()
    assert service.loaded_path is None


def test_service_survives_a_dataset_that_moved(one_of_each, tmp_path):
    copy = tmp_path / "submission"
    shutil.copytree(DATA, copy)

    service = EvidenceService()
    service.bundle_for(one_of_each["FAST_CLOSURE"], str(copy))
    shutil.rmtree(copy)

    service.reset()
    with pytest.raises(SourceUnavailable):
        service.bundle_for(one_of_each["FAST_CLOSURE"], str(copy))
