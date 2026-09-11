"""
Detection validation in the desktop application.

Validation measures SAT-SA's own RULES against a labelled dataset. It
is quality assurance about the detector, not an assessment of any
entity, and the distinction has to survive contact with the product:

  * it runs only where seeded labels exist, and their absence means
    "cannot be measured", never "measured and found correct";
  * it runs strictly after a finished assessment and cannot reach
    detection, scoring, evidence or the review queue;
  * it never presents synthetic agreement as expert review.
"""

import json
import os
import shutil

import pytest

from application.services.assessment_service import (
    VALIDATION_REPORT_FILE,
    AssessmentService,
)
from application.services.history_service import HistoryService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(DATA), reason="synthetic dataset not generated")


@pytest.fixture(scope="module")
def labelled_run(tmp_path_factory):
    """An assessment of a dataset that carries seeded labels."""
    history = HistoryService(tmp_path_factory.mktemp("labelled"))
    service = AssessmentService(history=history)
    result, _, _, run = service.run_assessment(DATA)
    return result, run


@pytest.fixture(scope="module")
def unlabelled_run(tmp_path_factory):
    """The real-submission case: the same data with its labels removed."""
    dataset = tmp_path_factory.mktemp("unlabelled") / "submission"
    shutil.copytree(DATA, dataset)
    os.remove(dataset / "ground_truth.json")

    history = HistoryService(tmp_path_factory.mktemp("unlabelled-runs"))
    service = AssessmentService(history=history)
    result, _, _, run = service.run_assessment(str(dataset))
    return result, run


# ----------------------------------------------------------------------
# It is produced, and it carries what a supervisor needs
# ----------------------------------------------------------------------

def test_a_labelled_dataset_produces_a_validation_report(labelled_run):
    _, run = labelled_run
    assert (run.path / VALIDATION_REPORT_FILE).is_file()


@pytest.mark.parametrize("needed", [
    "PER-RULE DETECTION",     # per-rule table
    "Prec",                   # precision
    "Recall",                 # recall
    "InScope",                # scope-aware recall
    "TP", "FP", "FN",         # the raw counts behind them
    "ALL RULES",              # the overall line
    "RULE SCOPES",            # why a scoped rule is not "missing" things
    "RANKING QUALITY",        # was the right thing prioritised
    "REVIEW EFFORT",          # review-effort reduction
])
def test_the_report_carries_every_required_measure(labelled_run, needed):
    _, run = labelled_run
    assert needed in (run.path / VALIDATION_REPORT_FILE).read_text()


def test_the_report_states_its_own_limits(labelled_run):
    """
    The single most important line. Synthetic agreement is not expert
    validation, and the report must say so without being asked.
    """
    _, run = labelled_run
    text = (run.path / VALIDATION_REPORT_FILE).read_text()
    assert "SYNTHETIC VALIDATION" in text
    assert "not against expert manual review" in text
    assert "partly circular" in text


def test_the_report_is_listed_as_a_run_artifact(labelled_run):
    """It must be reachable from the Reports page like any other output."""
    _, run = labelled_run
    names = {p.name for _, d in run.artifact_directories()
             for p in d.iterdir() if p.is_file()}
    assert VALIDATION_REPORT_FILE in names


# ----------------------------------------------------------------------
# A real submission has no labels
# ----------------------------------------------------------------------

def test_an_unlabelled_submission_produces_no_validation_report(unlabelled_run):
    """
    Absence of labels means the rules cannot be measured on this data.
    Writing an empty or default report would let a supervisor read it as
    a clean result.
    """
    _, run = unlabelled_run
    assert not (run.path / VALIDATION_REPORT_FILE).exists()


def test_an_unlabelled_submission_still_assesses_normally(unlabelled_run):
    result, _ = unlabelled_run
    assert result["run_metadata"]["total_findings"] > 0
    assert result["entities"]
    assert result["review_queue"]


# ----------------------------------------------------------------------
# It cannot influence the assessment
# ----------------------------------------------------------------------

def test_validation_changes_nothing_about_the_assessment(labelled_run,
                                                          unlabelled_run):
    """
    The same records assessed with and without labels must produce an
    identical assessment. If validation touched findings, scoring or the
    queue, these would diverge.
    """
    labelled, _ = labelled_run
    unlabelled, _ = unlabelled_run

    def fingerprint(result):
        return {
            "findings": result["run_metadata"]["total_findings"],
            "alerts": result["run_metadata"]["total_alerts"],
            "queue": len(result["review_queue"]),
            "ranking": [(e["soc_id"], e["priority_rank"],
                          e["supervisory_risk_score"])
                        for e in result["entities"]],
        }

    assert fingerprint(labelled) == fingerprint(unlabelled)


def test_no_finding_carries_a_validation_verdict(labelled_run):
    """
    Ground truth must never leak into the evidence a supervisor reads.
    A finding may not know whether it was 'seeded' or 'true positive'.
    """
    result, _ = labelled_run
    forbidden = {"true_positive", "false_positive", "ground_truth",
                 "injected", "seeded", "is_true_positive"}

    for entity in result["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            assert not (forbidden & set(finding)), finding.get("finding_type")
            evidence = finding.get("evidence") or {}
            if isinstance(evidence, dict):
                assert not (forbidden & set(evidence)), finding.get("rule_id")

    for item in result["review_queue"]:
        assert not (forbidden & set(item))


def test_the_stored_results_do_not_embed_validation(labelled_run):
    _, run = labelled_run
    stored = json.loads(run.results_path().read_text())
    assert "validation" not in stored
    assert "ground_truth" not in stored


# ----------------------------------------------------------------------
# A failure in validation must not cost an assessment
# ----------------------------------------------------------------------

def test_a_broken_ground_truth_file_does_not_fail_the_assessment(
        tmp_path_factory):
    dataset = tmp_path_factory.mktemp("broken") / "submission"
    shutil.copytree(DATA, dataset)
    (dataset / "ground_truth.json").write_text("{ not valid json")

    history = HistoryService(tmp_path_factory.mktemp("broken-runs"))
    result, _, _, run = AssessmentService(history=history).run_assessment(
        str(dataset))

    assert result["run_metadata"]["total_findings"] > 0
    assert run.results_path().is_file()
