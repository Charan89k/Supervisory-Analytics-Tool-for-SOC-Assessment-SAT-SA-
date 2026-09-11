"""
Reporting tests.

The report is what leaves the tool — it is shared, filed, and read by
people who never see the application. Two rules govern it:

  1. It must reflect what the product actually does. The PDF spent
     several phases describing a Phase-4 product: no capability
     standing, no benchmarking, no trends, no validation record.
  2. A section appears only when the data supports it. An empty
     "Peer Benchmarking" heading implies the tool looked and found
     nothing comparable, which is a different claim from not having
     been able to look.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

from analytics.reporting import write_csv_exports, write_pdf_report
from analytics.trends import build_trends

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(DATA), reason="synthetic dataset not generated")

HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None


@pytest.fixture(scope="module")
def config():
    with open(CONFIG) as handle:
        return yaml.safe_load(handle)


def pdf_text(path) -> str:
    """
    Extract text with whitespace collapsed.

    pdftotext wraps at the page's line breaks, so a sentence that reads
    continuously on the page arrives split across lines. These
    assertions are about what the report says, not how it is laid out,
    so the wrapping is normalised away.
    """
    if not HAVE_PDFTOTEXT:
        pytest.skip("pdftotext not available")
    result = subprocess.run(["pdftotext", str(path), "-"],
                             capture_output=True, text=True)
    return " ".join(result.stdout.split())


# ---------------------------------------------------------------------------
# The report reflects the current product
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def report(assessment, config, tmp_path_factory):
    out = tmp_path_factory.mktemp("report")
    return write_pdf_report(str(out), assessment, config), assessment


def test_report_is_written(report):
    path, _ = report
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 5_000


@pytest.mark.parametrize("section", [
    "Executive",
    "Dataset Validation",
    "Entity Ranking",
    "Peer Benchmarking",
    "Supervisory Capability Areas",
    "Risk Drivers",
    "Major Execution Gaps",
    "Negative Space",
])
def test_report_contains_section(report, section):
    path, _ = report
    assert section in pdf_text(path), section


def test_report_states_findings_are_indicators_not_determinations(report):
    path, _ = report
    text = pdf_text(path)
    assert "require supervisory review" in text
    assert "not, on their own, determinations" in text


def test_benchmarking_section_carries_its_caveat(report):
    """Deviation is an indicator, never a verdict — including on paper."""
    path, _ = report
    text = pdf_text(path)
    assert "indicator for review, not a finding" in text


def test_capability_section_does_not_claim_verification(report):
    path, _ = report
    text = pdf_text(path)
    assert "no rule fired" in text
    assert "not that the capability was independently verified" in text


def test_validation_section_reports_a_clean_submission(report):
    path, _ = report
    assert "passed structural validation" in pdf_text(path)


def test_report_does_not_invent_recommendations(report):
    """
    There is no recommendation-generation logic in the pipeline, and the
    narration layer restates findings rather than deciding what to do
    about them. A "Recommended Actions" heading would misrepresent the
    tool.
    """
    path, _ = report
    assert "Recommended Action" not in pdf_text(path)


# ---------------------------------------------------------------------------
# Sections appear only when the data supports them
# ---------------------------------------------------------------------------

def test_trends_are_absent_for_a_single_period(report):
    path, _ = report
    assert "Trends Across Submission Periods" not in pdf_text(path)


def test_trends_appear_when_periods_were_compared(assessment, config,
                                                    tmp_path):
    """Built from two real per-period assessments, not a stub."""
    trend = build_trends([("2026-Q1", assessment), ("2026-Q2", assessment)])
    assert trend.available

    path = write_pdf_report(str(tmp_path), assessment, config,
                             trend_report=trend)
    text = pdf_text(path)
    assert "Trends Across Submission Periods" in text
    assert "submission periods" in text


def test_benchmarking_is_omitted_when_no_group_is_comparable(config,
                                                               tmp_path):
    """
    An empty heading would imply the tool looked and found nothing
    comparable, which is a different claim from not having been able to
    look. With one entity per group there is nothing to compare.
    """
    results = {
        "run_metadata": {"entities_assessed": 2, "total_alerts": 10,
                          "total_findings": 0},
        "validation_report": {"issue_count": 0},
        "entities": [
            {"soc_id": "SOC-1", "organization_name": "A",
             "peer_group": "TELECOM", "priority_rank": 1,
             "supervisory_risk_score": 5.0, "execution_gap_findings": [],
             "negative_space_findings": [], "score_breakdown": []},
            {"soc_id": "SOC-2", "organization_name": "B",
             "peer_group": "ENERGY", "priority_rank": 2,
             "supervisory_risk_score": 3.0, "execution_gap_findings": [],
             "negative_space_findings": [], "score_breakdown": []},
        ],
        "review_queue": [],
    }
    text = pdf_text(write_pdf_report(str(tmp_path), results, config))
    assert "not a benchmark" in text


def test_capability_section_is_omitted_without_a_mapping(assessment,
                                                           tmp_path):
    path = write_pdf_report(str(tmp_path), assessment, config={})
    assert "Supervisory Capability Areas" not in pdf_text(path)


def test_validation_issues_are_listed_when_present(assessment, config,
                                                     tmp_path):
    results = dict(assessment)
    results["validation_report"] = {
        "issue_count": 1, "error_count": 0, "warning_count": 1,
        "issues": [{"table": "alerts", "severity": "WARNING",
                     "message": "unrecognized severity value(s)",
                     "row_count": 3}],
    }
    text = pdf_text(write_pdf_report(str(tmp_path), results, config))
    assert "unrecognized severity value" in text
    assert "WARNING" in text


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------

def test_csv_exports_are_written(tmp_path):
    import pandas as pd

    written = write_csv_exports(
        str(tmp_path),
        pd.DataFrame([{"soc_id": "SOC-1", "supervisory_risk_score": 1.0}]),
        pd.DataFrame([{"soc_id": "SOC-1", "finding_type": "SLOW_TRIAGE",
                        "evidence": {"a": 1}}]),
        pd.DataFrame(),
        pd.DataFrame([{"queue_rank": 1, "soc_id": "SOC-1"}]),
    )
    assert len(written) == 4
    for path in written:
        assert os.path.isfile(path)


def test_evidence_dicts_survive_the_csv_round_trip(tmp_path):
    """
    An evidence dict does not round-trip cleanly to CSV, so it is
    stringified rather than silently flattened into unusable columns.
    """
    import pandas as pd

    write_csv_exports(
        str(tmp_path), pd.DataFrame(),
        pd.DataFrame([{"finding_type": "X", "evidence": {"minutes": 2.0}}]),
        pd.DataFrame(), pd.DataFrame())

    frame = pd.read_csv(tmp_path / "execution_gap_findings.csv")
    assert "minutes" in str(frame.iloc[0]["evidence"])


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------

def test_reporting_does_not_depend_on_the_application_layer():
    """
    The report is produced by the pipeline, which must run headless.
    analytics importing from application/ would invert the dependency
    and break a core-only install, which carries no PySide6.
    """
    import inspect

    from analytics import benchmarking, capabilities, reporting

    for module in (reporting, benchmarking, capabilities):
        source = inspect.getsource(module)
        assert "from application" not in source, module.__name__
        assert "import application" not in source, module.__name__
        assert "PySide6" not in source, module.__name__
