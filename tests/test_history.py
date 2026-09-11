"""
Assessment history tests.

The property these defend is immutability. Before this, the desktop
application wrote every assessment to one directory, so running a
second one destroyed the first — its findings, evidence exports and PDF
gone, with no warning. A supervisory assessment is a record that may be
referred back to or produced as the basis for a finding, so the tests
below are mostly about what must NOT happen to an existing run.
"""

import json
import os
from datetime import datetime

import pytest

from application.services.history_service import (
    ENV_HISTORY_DIR,
    SUMMARY_FILE,
    AssessmentRun,
    HistoryService,
    RunSummary,
)


@pytest.fixture
def history(tmp_path):
    return HistoryService(tmp_path / "assessments")


def results(entities=1, execution=3, negative=1, queue=2, alerts=100,
             findings=None):
    total = findings if findings is not None else (execution + negative) * entities
    return {
        "run_metadata": {"total_alerts": alerts, "total_findings": total},
        "validation_report": {"issue_count": 0},
        "entities": [
            {"soc_id": f"SOC-{i}",
             "execution_gap_findings": [{}] * execution,
             "negative_space_findings": [{}] * negative}
            for i in range(entities)
        ],
        "review_queue": [{"queue_rank": i + 1} for i in range(queue)],
    }


# ---------------------------------------------------------------------------
# A completed run is never overwritten
# ---------------------------------------------------------------------------

def test_each_run_gets_its_own_directory(history):
    first = history.create_run("/data/q1", "q1")
    second = history.create_run("/data/q1", "q1")
    assert first.path != second.path


def test_runs_started_in_the_same_second_do_not_collide(history):
    """
    Two assessments a moment apart share a timestamp. Without a
    collision suffix the second would open the first's directory and
    overwrite it.
    """
    runs = [history.create_run("/data", "d") for _ in range(4)]
    assert len({r.path for r in runs}) == 4
    assert len({r.run_id for r in runs}) == 4


def test_create_run_never_returns_an_existing_directory(history):
    first = history.create_run("/data", "d")
    (first.path / "assessment_results.json").write_text('{"marker": 1}')

    for _ in range(3):
        later = history.create_run("/data", "d")
        assert later.path != first.path

    # The first run's contents are untouched.
    assert json.loads(
        (first.path / "assessment_results.json").read_text())["marker"] == 1


def test_an_earlier_run_survives_a_later_one(history):
    first = history.create_run("/data/q1", "q1")
    (first.path / "assessment_results.json").write_text(
        json.dumps(results(entities=2)))
    history.finalise(first, results(entities=2))

    second = history.create_run("/data/q2", "q2")
    (second.path / "assessment_results.json").write_text(
        json.dumps(results(entities=5)))
    history.finalise(second, results(entities=5))

    stored = {r.run_id: r for r in history.list_runs()}
    assert len(stored) == 2
    assert stored[first.run_id].summary.entities == 2
    assert stored[second.run_id].summary.entities == 5


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_no_history_lists_nothing(history):
    assert history.list_runs() == []
    assert history.latest() is None


def test_unfinalised_runs_are_not_listed(history):
    """
    An interrupted run has no summary. Listing it would put a run with
    no findings in the history as though it had completed.
    """
    history.create_run("/data", "d")
    assert history.list_runs() == []


def test_a_corrupt_summary_is_skipped_not_guessed(history):
    good = history.create_run("/data", "d")
    history.finalise(good, results())

    bad = history.create_run("/data", "d")
    (bad.path / SUMMARY_FILE).write_text("{ not json")

    listed = [r.run_id for r in history.list_runs()]
    assert listed == [good.run_id]


def test_runs_are_listed_newest_first(history):
    created = []
    for _ in range(3):
        run = history.create_run("/data", "d")
        history.finalise(run, results())
        created.append(run.run_id)

    listed = [r.run_id for r in history.list_runs()]
    assert listed == sorted(created, reverse=True)
    assert history.latest().run_id == listed[0]


def test_get_returns_a_named_run(history):
    run = history.create_run("/data", "d")
    history.finalise(run, results())
    assert history.get(run.run_id).run_id == run.run_id


def test_get_returns_none_for_an_unknown_run(history):
    assert history.get("2020-01-01_000000") is None


# ---------------------------------------------------------------------------
# The summary describes the assessment it belongs to
# ---------------------------------------------------------------------------

def test_summary_is_derived_from_the_results(history):
    """
    Derived rather than passed in, so the history list cannot disagree
    with the assessment it describes.
    """
    run = history.create_run("/data/q1", "q1")
    history.finalise(run, results(entities=3, execution=4, negative=2,
                                   queue=7, alerts=900))

    summary = history.list_runs()[0].summary
    assert summary.entities == 3
    assert summary.execution_gaps == 12
    assert summary.negative_space == 6
    assert summary.review_queue == 7
    assert summary.total_alerts == 900
    assert summary.dataset_label == "q1"


def test_summary_records_ai_use_only_when_ai_ran(history):
    payload = results(queue=3)
    payload["review_queue"][0]["narration_source"] = "mock"
    payload["review_queue"][1]["narration_source"] = "rule"

    run = history.create_run("/data", "d")
    history.finalise(run, payload, ai_backend="mock")

    summary = history.list_runs()[0].summary
    assert summary.ai_backend == "mock"
    assert summary.ai_explained == 1


def test_summary_records_periods_for_a_multi_period_run(history):
    class Report:
        periods = ["2026-Q1", "2026-Q2", "2026-Q3"]

    run = history.create_run("/data", "d")
    history.finalise(run, results(), trend_report=Report())

    summary = history.list_runs()[0].summary
    assert summary.multi_period is True
    assert "3 periods" in summary.label()


def test_single_period_run_is_labelled_as_such(history):
    run = history.create_run("/data", "d")
    history.finalise(run, results())
    assert "single period" in history.list_runs()[0].summary.label()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_results_are_read_back_verbatim(history):
    """
    A past assessment must show what it actually said, not what the
    current rule set would say about the same data now.
    """
    payload = results(entities=2)
    payload["marker"] = "original"

    run = history.create_run("/data", "d")
    (run.path / "assessment_results.json").write_text(json.dumps(payload))
    history.finalise(run, payload)

    loaded = history.load_results(history.list_runs()[0])
    assert loaded["marker"] == "original"
    assert len(loaded["entities"]) == 2


def test_loading_a_run_with_no_results_returns_none(history):
    run = history.create_run("/data", "d")
    history.finalise(run, results())
    assert history.load_results(history.list_runs()[0]) is None


def test_loading_a_corrupt_results_file_returns_none(history):
    run = history.create_run("/data", "d")
    (run.path / "assessment_results.json").write_text("{ broken")
    history.finalise(run, results())
    assert history.load_results(history.list_runs()[0]) is None


# ---------------------------------------------------------------------------
# Deletion is explicit and never automatic
# ---------------------------------------------------------------------------

def test_delete_removes_only_the_named_run(history):
    keep = history.create_run("/data", "keep")
    history.finalise(keep, results())
    drop = history.create_run("/data", "drop")
    history.finalise(drop, results())

    assert history.delete(history.get(drop.run_id)) is True
    assert [r.run_id for r in history.list_runs()] == [keep.run_id]
    assert keep.path.is_dir()


def test_history_is_never_pruned_automatically(history):
    """
    No retention limit and no scheduled cleanup: deciding an assessment
    no longer matters is a supervisory decision, not the tool's.
    """
    for _ in range(12):
        run = history.create_run("/data", "d")
        history.finalise(run, results())
    assert len(history.list_runs()) == 12


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def test_environment_overrides_the_history_location(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_HISTORY_DIR, str(tmp_path / "elsewhere"))
    assert HistoryService().root == tmp_path / "elsewhere"


def test_explicit_root_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_HISTORY_DIR, str(tmp_path / "env"))
    assert HistoryService(tmp_path / "explicit").root == tmp_path / "explicit"


def test_assessment_service_writes_into_history(tmp_path, monkeypatch):
    """
    The service must allocate a run directory rather than writing to a
    fixed path — that fixed path was the data-loss bug.
    """
    monkeypatch.setenv(ENV_HISTORY_DIR, str(tmp_path / "assessments"))
    from application.services.assessment_service import AssessmentService

    service = AssessmentService()
    assert service.history.root == tmp_path / "assessments"
    assert not hasattr(service, "outputs_dir")
