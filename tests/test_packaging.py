"""
Packaging behaviour: path resolution and the installation self-check.

A frozen application fails differently from a source checkout, and the
most damaging failure is silent — writing assessment history into
PyInstaller's temporary unpack directory succeeds, and then the history
disappears when the process exits. These tests simulate the frozen case
rather than waiting for a build to prove it.
"""

import io
import sys

import pytest

from application import paths
from application.self_check import run_self_check


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Make the application believe it is running from a bundle."""
    unpack = tmp_path / "_MEI123456"
    unpack.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(unpack), raising=False)
    return unpack


# ----------------------------------------------------------------------
# Read-only resources
# ----------------------------------------------------------------------

def test_from_source_resources_are_the_project():
    assert not paths.is_frozen()
    assert paths.config_path().is_file()
    assert paths.config_path().name == "assessment_rules.yaml"


def test_frozen_resources_come_from_the_bundle(frozen):
    assert paths.resource_root() == frozen
    assert paths.config_path() == frozen / "config" / "assessment_rules.yaml"


def test_frozen_without_meipass_falls_back_to_the_executable(monkeypatch):
    """
    A onefile bundle sets _MEIPASS; other freezers may not. Resources
    then sit beside the executable, which is still a real answer.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert paths.resource_root() == paths.Path(sys.executable).resolve().parent


# ----------------------------------------------------------------------
# Writable storage
# ----------------------------------------------------------------------

def test_history_never_lives_inside_the_bundle(frozen):
    """
    The silent-data-loss case. Writing history into the unpack
    directory works perfectly until the application exits, at which
    point every stored assessment is gone and nothing reported it.
    """
    history = paths.default_history_root()
    assert frozen not in history.parents, history
    assert str(frozen) not in str(history), history


def test_frozen_history_is_under_the_user_profile(frozen, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    history = paths.default_history_root()
    assert history.is_relative_to(paths.Path.home())
    assert history.name == "assessments"
    assert paths.APP_DIR_NAME in history.parts


def test_history_root_honours_an_explicit_setting(tmp_path):
    """A deployment that pins storage must win over every default."""
    from application.services.history_service import HistoryService
    assert HistoryService(tmp_path / "elsewhere").root == tmp_path / "elsewhere"


# ----------------------------------------------------------------------
# Self-check
# ----------------------------------------------------------------------

def test_self_check_passes_on_a_working_install(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "default_history_root",
                        lambda: tmp_path / "assessments")

    stream = io.StringIO()
    code = run_self_check(stream=stream)
    report = stream.getvalue()

    assert code == 0, report
    assert "FAIL" not in report, report
    for expected in ("Rule configuration", "Analytics engine",
                      "Explanation backends", "Writable storage"):
        assert expected in report

    # The AI line must be present and must not decide the outcome.
    assert "Local AI (optional" in report
    assert "no external connection" in report


def test_self_check_writes_a_readable_report(monkeypatch, tmp_path):
    """
    A packaged Windows build is windowed and has no console, so the
    file is the only copy the operator can actually read.
    """
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "default_history_root",
                        lambda: tmp_path / "assessments")

    run_self_check(stream=io.StringIO())
    written = (tmp_path / "self-check.txt").read_text(encoding="utf-8")
    assert "installation self-check" in written
    assert "Rule configuration" in written


def test_self_check_reports_a_broken_install_instead_of_crashing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "config_path",
                        lambda: tmp_path / "absent" / "rules.yaml")

    stream = io.StringIO()
    code = run_self_check(stream=stream)
    report = stream.getvalue()

    assert code == 1
    assert "FAIL" in report
    assert "not ready" in report


def test_ai_absence_is_not_a_self_check_failure(monkeypatch, tmp_path,
                                                 chooses_own_backend):
    """
    The deterministic assessment is the product. A machine with no AI
    runtime is a fully working install, and the report must say so.

    chooses_own_backend lifts the suite-wide mock forcing; without it
    the probe would resolve to the sample explainer and this would pass
    without ever exercising the absent-runtime path it claims to test.
    """
    from analytics.narration import ollama_runtime
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "default_history_root",
                        lambda: tmp_path / "assessments")
    monkeypatch.setattr(ollama_runtime, "find_executable", lambda: None)
    monkeypatch.setattr(ollama_runtime, "query_service",
                        lambda *a, **k: (False, []))

    stream = io.StringIO()
    code = run_self_check(stream=stream)
    report = stream.getvalue()

    assert code == 0, report
    assert "All checks passed" in report
    assert "AI NOT INSTALLED" in report, report
    assert "SAT-SA-Setup-AI" in report, "the report must name the fix"
