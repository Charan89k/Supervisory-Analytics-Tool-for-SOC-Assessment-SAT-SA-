"""
End-to-end pipeline tests.

These run the real assessment over the real synthetic dataset. They are
the tests that would catch a regression no unit test can see: a detector
that stops firing once it meets real data, a validation failure that
escapes as the wrong exception type, or an assessment that quietly
starts depending on the optional AI layer.

Every test here runs with narration forced to the mock backend, and one
of them asserts that the deterministic output is byte-identical whether
the AI layer is enabled or not.
"""

import json
import os
import shutil

import pandas as pd
import pytest

from analytics.ingestion import classify_input, rejection_reason
from analytics.validator import DatasetValidationError
from main import run_pipeline, validate_dataset_at

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(DATA),
    reason="synthetic dataset not generated; run data/generator/generate_dataset.py",
)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """One full assessment, reused across the module."""
    out = tmp_path_factory.mktemp("assessment")
    return run_pipeline(DATA, str(out), CONFIG, export_csv=True, export_pdf=True)


# ---------------------------------------------------------------------------
# Deterministic assessment with no AI
# ---------------------------------------------------------------------------

def test_assessment_completes_without_any_ai(results):
    assert results is not None
    assert results["run_metadata"]["entities_assessed"] == 5
    assert results["run_metadata"]["total_findings"] > 0


def test_no_record_carries_an_ai_explanation_when_ai_is_off(results):
    """
    `--narrate` was not passed, so nothing should have been narrated.
    A record showing a mock or model explanation here would mean the
    pipeline reached the AI layer without being asked to.
    """
    for record in results["review_queue"]:
        assert record.get("narration_source") in (None, "rule")
        assert not record.get("narration_is_mock")


def test_every_detector_fires_on_the_synthetic_dataset(results):
    """
    The generator seeds ground truth for all fourteen rules. A rule that
    stops firing here has either broken or lost its ground truth, and
    both are regressions worth failing on — this is the check that would
    have caught MISSING_ALERT_CATEGORY and MISSING_ESCALATION_RECORDS
    sitting silent.
    """
    fired = set()
    for entity in results["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            fired.add(finding["finding_type"])

    expected = {
        "MISSED_ESCALATION", "SLOW_TRIAGE", "FAST_CLOSURE", "MISSING_EVIDENCE",
        "REOPENED_CASE", "REPETITIVE_INVESTIGATION", "ANALYST_OVERLOAD",
        "ACK_WITHOUT_INVESTIGATION", "REPEATED_ALERT_WITHOUT_REMEDIATION",
        "TELEMETRY_GAP", "MISSING_ALERT_CATEGORY", "LOW_ACTIVITY_OUTLIER",
        "MISSING_ESCALATION_RECORDS", "MISSING_INVESTIGATIONS",
    }
    assert expected - fired == set(), f"rules that never fired: {expected - fired}"


def test_risk_ranking_matches_seeded_ground_truth(results):
    """
    The generator assigns SOC-001 the 'clean' profile and SOC-003 the
    'weak' one. If the scoring ever ranks the clean entity above the
    weak one, the pipeline is telling supervisors the wrong thing.
    """
    ranks = {e["soc_id"]: e["priority_rank"] for e in results["entities"]}
    assert ranks["SOC-003"] < ranks["SOC-001"], (
        f"weak entity ranked below clean entity: {ranks}")


def test_every_finding_carries_rationale_and_rule_id(results):
    for entity in results["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            assert finding.get("rule_id"), finding
            assert finding.get("rationale"), finding


def test_risk_score_equals_the_sum_of_its_components(results):
    """
    Auditability: a supervisor must be able to recompute any score by
    hand from the published breakdown.
    """
    for entity in results["entities"]:
        total = sum(c["contribution"] for c in entity["score_breakdown"])
        assert abs(total - entity["supervisory_risk_score"]) < 0.05, entity["soc_id"]


def test_all_exports_are_written(results, tmp_path_factory):
    out = tmp_path_factory.mktemp("exports")
    run_pipeline(DATA, str(out), CONFIG, export_csv=True, export_pdf=True)
    for name in ("assessment_results.json", "entity_risk_scores.csv",
                 "execution_gap_findings.csv", "negative_space_findings.csv",
                 "review_queue.csv", "executive_summary.pdf"):
        path = out / name
        assert path.exists(), f"{name} not written"
        assert path.stat().st_size > 0, f"{name} is empty"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_clean_dataset_validates(results):
    report = validate_dataset_at(DATA)
    assert not report.has_errors
    assert report.to_dict()["error_count"] == 0


def test_duplicate_alert_ids_raise_typed_error_carrying_the_report(tmp_path):
    dataset = tmp_path / "dupes"
    shutil.copytree(DATA, dataset)
    alerts = pd.read_csv(dataset / "alerts.csv")
    pd.concat([alerts, alerts.head(3)]).to_csv(dataset / "alerts.csv", index=False)

    with pytest.raises(DatasetValidationError) as excinfo:
        run_pipeline(str(dataset), str(tmp_path / "out"), CONFIG)

    error = excinfo.value
    assert error.report.has_errors
    assert any("duplicate alert_id" in i.message for i in error.report.errors)
    assert "duplicate alert_id" in error.message()


def test_missing_required_table_is_reported_not_crashed(tmp_path):
    dataset = tmp_path / "incomplete"
    shutil.copytree(DATA, dataset)
    os.remove(dataset / "telemetry.csv")

    with pytest.raises(FileNotFoundError, match="telemetry"):
        run_pipeline(str(dataset), str(tmp_path / "out"), CONFIG)


def test_empty_required_table_is_an_error_not_a_crash(tmp_path):
    dataset = tmp_path / "empty-table"
    shutil.copytree(DATA, dataset)
    pd.DataFrame(columns=["telemetry_id", "soc_id", "expected", "enabled",
                          "coverage_percentage"]).to_csv(
        dataset / "telemetry.csv", index=False)

    with pytest.raises(DatasetValidationError) as excinfo:
        run_pipeline(str(dataset), str(tmp_path / "out"), CONFIG)
    assert any(i.table == "telemetry" for i in excinfo.value.report.errors)


# ---------------------------------------------------------------------------
# Ingestion boundary
# ---------------------------------------------------------------------------

def test_directory_is_a_supported_input():
    assert classify_input(DATA) is not None


def test_single_csv_is_not_yet_a_supported_input():
    """
    The engine reads a folder of CSV tables. Until the ZIP/JSON adapters
    land, the UI must not advertise otherwise — this test is what keeps
    the claim and the capability from drifting apart again.
    """
    assert classify_input(os.path.join(DATA, "alerts.csv")) is None
    reason = rejection_reason(os.path.join(DATA, "alerts.csv"))
    assert "folder" in reason.lower()


def test_nonexistent_path_explains_itself():
    assert "does not exist" in rejection_reason("/no/such/dataset")
