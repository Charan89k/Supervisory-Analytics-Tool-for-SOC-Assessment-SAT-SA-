"""
Report discovery across single- and multi-period assessments.

A multi-period assessment writes its cross-period artifacts at the run
root and each period's own outputs under periods/<name>/. Listing only
the root hid four of eight artifacts — every CSV export — from anyone
who assessed more than one period, leaving them to browse internal
directories by hand for outputs the tool had already produced.

These tests hold that every artifact SAT-SA writes is reachable from
the run, and that the two levels stay distinguishable.
"""

import json

import pytest

from application.services.history_service import AssessmentRun, RunSummary

ROOT_ARTIFACTS = ["assessment_results.json", "executive_summary.pdf",
                  "trend_report.json", "run.json"]
PERIOD_ARTIFACTS = ["assessment_results.json", "executive_summary.pdf",
                    "entity_risk_scores.csv", "execution_gap_findings.csv",
                    "negative_space_findings.csv", "review_queue.csv"]


def make_run(tmp_path, periods=(), root_files=ROOT_ARTIFACTS,
              period_files=PERIOD_ARTIFACTS):
    """A run directory laid out the way the pipeline lays one out."""
    path = tmp_path / "2026-01-01_120000"
    path.mkdir()
    for name in root_files:
        (path / name).write_text("x")

    for label in periods:
        period = path / "periods" / label
        period.mkdir(parents=True)
        for name in period_files:
            (period / name).write_text("x")

    summary = RunSummary(run_id=path.name, periods=list(periods) or ["single"])
    return AssessmentRun(summary=summary, path=path)


# ----------------------------------------------------------------------
# Single period
# ----------------------------------------------------------------------

def test_a_single_period_run_has_no_period_directories(tmp_path):
    run = make_run(tmp_path)
    assert run.period_directories() == []
    assert [label for label, _ in run.artifact_directories()] == [None]


def test_a_single_period_run_exposes_its_artifacts_from_the_root(tmp_path):
    run = make_run(tmp_path)
    (_, directory), = run.artifact_directories()
    present = {entry.name for entry in directory.iterdir() if entry.is_file()}
    assert present == set(ROOT_ARTIFACTS)


# ----------------------------------------------------------------------
# Multiple periods — the defect this fixes
# ----------------------------------------------------------------------

def test_every_period_directory_is_discovered(tmp_path):
    run = make_run(tmp_path, periods=["2025-Q3", "2025-Q4", "2026-Q1"])
    assert [label for label, _ in run.period_directories()] == [
        "2025-Q3", "2025-Q4", "2026-Q1"]


def test_period_csv_exports_are_reachable(tmp_path):
    """
    The exact artifacts that were unreachable: the CSV exports live
    only under periods/, and a supervisor must not have to open a file
    manager to find them.
    """
    run = make_run(tmp_path, periods=["2025-Q3", "2025-Q4"])

    reachable = set()
    for _, directory in run.artifact_directories():
        reachable |= {entry.name for entry in directory.iterdir()
                      if entry.is_file()}

    for csv in ("entity_risk_scores.csv", "execution_gap_findings.csv",
                 "negative_space_findings.csv", "review_queue.csv"):
        assert csv in reachable, csv


def test_nothing_the_pipeline_wrote_is_unreachable(tmp_path):
    """Walk the run on disk and the discovery; they must agree."""
    run = make_run(tmp_path, periods=["2025-Q3", "2025-Q4", "2026-Q1"])

    on_disk = {p for p in run.path.rglob("*") if p.is_file()}
    discovered = {entry
                  for _, directory in run.artifact_directories()
                  for entry in directory.iterdir() if entry.is_file()}
    assert on_disk == discovered


def test_the_two_levels_stay_distinguishable(tmp_path):
    """
    Assessment-level and period-level outputs share filenames. A
    supervisor reading review_queue.csv must be able to tell which
    period it covers.
    """
    run = make_run(tmp_path, periods=["2025-Q3", "2025-Q4"])
    labels = [label for label, _ in run.artifact_directories()]
    assert labels == [None, "2025-Q3", "2025-Q4"]

    paths = [directory for _, directory in run.artifact_directories()]
    assert len(set(paths)) == len(paths), "a directory was listed twice"


def test_periods_are_ordered_oldest_first(tmp_path):
    run = make_run(tmp_path, periods=["2026-Q1", "2025-Q3", "2025-Q4"])
    assert [label for label, _ in run.period_directories()] == [
        "2025-Q3", "2025-Q4", "2026-Q1"]


# ----------------------------------------------------------------------
# Degenerate layouts
# ----------------------------------------------------------------------

def test_an_empty_period_directory_is_not_listed(tmp_path):
    """An empty folder is not a period a supervisor can open anything in."""
    run = make_run(tmp_path, periods=["2025-Q3"], period_files=[])
    assert run.period_directories() == []


def test_a_missing_periods_directory_is_not_an_error(tmp_path):
    run = make_run(tmp_path)
    assert run.period_directories() == []


def test_a_run_missing_an_expected_artifact_still_lists_the_rest(tmp_path):
    run = make_run(tmp_path, root_files=["assessment_results.json"])
    (_, directory), = run.artifact_directories()
    present = {e.name for e in directory.iterdir() if e.is_file()}
    assert present == {"assessment_results.json"}
    assert not (directory / "executive_summary.pdf").exists()


def test_a_stray_file_among_the_period_directories_is_ignored(tmp_path):
    run = make_run(tmp_path, periods=["2025-Q3"])
    (run.path / "periods" / "notes.txt").write_text("x")
    assert [label for label, _ in run.period_directories()] == ["2025-Q3"]


# ----------------------------------------------------------------------
# A historical run behaves the same as the one just produced
# ----------------------------------------------------------------------

def test_a_run_loaded_from_history_discovers_the_same_artifacts(tmp_path):
    from application.services.history_service import HistoryService

    built = make_run(tmp_path, periods=["2025-Q3", "2025-Q4"])
    (built.path / "run.json").write_text(json.dumps({
        "run_id": built.run_id, "periods": ["2025-Q3", "2025-Q4"],
        "entities": 3, "total_findings": 10,
    }))

    loaded = HistoryService(tmp_path).list_runs()[0]
    assert loaded.run_id == built.run_id
    assert ([label for label, _ in loaded.artifact_directories()]
            == [label for label, _ in built.artifact_directories()])
