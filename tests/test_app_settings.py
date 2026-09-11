"""
Application settings tests.

The rule these defend: a setting that looks configurable but has no
effect is worse than no setting at all — it tells a supervisor they
have control they do not have, and the first time it matters they will
have relied on it. Every test below either checks persistence or
checks that a setting genuinely reaches the behaviour it names.
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from analytics.ingestion import resolve_dataset
from analytics.ingestion.errors import UnsafeArchiveError
from analytics.ingestion.limits import DEFAULT_LIMITS
from analytics.validator import DatasetValidationError
from application.services.ai_config import AIConfig
from application.services.app_settings import GIGABYTE, AppSettings
from application.services.assessment_service import AssessmentService
from application.services.settings_service import SettingsService

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")

needs_data = pytest.mark.skipif(
    not os.path.isdir(DATA), reason="synthetic dataset not generated")


# ---------------------------------------------------------------------------
# Defaults and persistence
# ---------------------------------------------------------------------------

def test_defaults_are_conservative():
    settings = AppSettings()
    assert settings.history_directory == ""      # use the default location
    assert settings.generate_csv_exports is True
    assert settings.generate_pdf_report is True
    assert settings.strict_validation is False   # warnings do not block


def test_round_trip(tmp_path):
    service = SettingsService(tmp_path / "settings.json")
    settings = AppSettings(
        history_directory="/runs", reopen_last_page=False,
        generate_csv_exports=False, generate_pdf_report=False,
        strict_validation=True, max_archive_gigabytes=0.5,
        max_archive_members=250)

    service.save_app_settings(settings)
    loaded = service.load_app_settings()

    assert loaded.history_directory == "/runs"
    assert loaded.reopen_last_page is False
    assert loaded.generate_csv_exports is False
    assert loaded.strict_validation is True
    assert loaded.max_archive_gigabytes == 0.5
    assert loaded.max_archive_members == 250


def test_app_and_ai_sections_coexist(tmp_path):
    from application.services.ai_config import create_ai_config

    path = tmp_path / "settings.json"
    service = SettingsService(path)
    service.save_ai_config(create_ai_config(enabled=True, backend="llamacpp"))
    service.save_app_settings(AppSettings(strict_validation=True))

    # A real backend, deliberately: "mock" is never persisted — see
    # test_the_mock_backend_is_never_persisted. This test is about the
    # two sections coexisting, not about which backend was chosen.
    assert service.load_ai_config().backend == "llamacpp"
    assert service.load_app_settings().strict_validation is True
    assert set(json.loads(path.read_text())) == {"ai", "app"}


def test_unknown_keys_are_ignored(tmp_path):
    """A file from a newer build must still load, not refuse to start."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(
        {"app": {"strict_validation": True, "a_future_setting": 42}}))
    assert SettingsService(path).load_app_settings().strict_validation is True


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json")
    assert SettingsService(path).load_app_settings() == AppSettings()


def test_missing_file_returns_defaults(tmp_path):
    assert SettingsService(tmp_path / "none.json").load_app_settings() \
        == AppSettings()


# ---------------------------------------------------------------------------
# Each setting reaches the behaviour it names
# ---------------------------------------------------------------------------

def test_history_directory_reaches_the_history_service(tmp_path):
    root = tmp_path / "custom-history"
    service = AssessmentService(
        settings=AppSettings(history_directory=str(root)))
    assert service.history.root == root


def test_empty_history_directory_uses_the_default(tmp_path):
    service = AssessmentService(settings=AppSettings(history_directory=""))
    assert service.history.root.name == "assessments"


def test_archive_limits_reach_the_ingestion_layer():
    settings = AppSettings(max_archive_gigabytes=0.25, max_archive_members=77)
    limits = settings.ingestion_limits()

    assert limits.max_total_uncompressed_bytes == int(0.25 * GIGABYTE)
    assert limits.max_members == 77
    # A single member may never exceed the whole-archive budget.
    assert limits.max_member_uncompressed_bytes <= limits.max_total_uncompressed_bytes


def test_member_limit_actually_rejects_an_archive(tmp_path):
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for index in range(60):
            handle.writestr(f"file{index}.csv", "x")

    permissive = AppSettings(max_archive_members=10_000)
    strict = AppSettings(max_archive_members=50)

    # Permissive: rejected later, on content, not on member count.
    with pytest.raises(Exception) as permissive_error:
        resolve_dataset(str(archive), permissive.ingestion_limits())
    assert "entries" not in str(permissive_error.value)

    with pytest.raises(UnsafeArchiveError, match="entries"):
        resolve_dataset(str(archive), strict.ingestion_limits())


def test_size_limit_actually_rejects_an_archive(tmp_path):
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("alerts.csv", "A" * 2_000_000)

    tiny = AppSettings(max_archive_gigabytes=0.0009)  # ~1 MB
    with pytest.raises(UnsafeArchiveError):
        resolve_dataset(str(archive), tiny.ingestion_limits())


def test_compression_ratio_guard_is_preserved():
    """
    A configurable size limit must not become a way to disable the
    zip-bomb guard by raising the budget.
    """
    settings = AppSettings(max_archive_gigabytes=64.0)
    assert settings.ingestion_limits().max_compression_ratio == \
        DEFAULT_LIMITS.max_compression_ratio


@needs_data
def test_strict_validation_changes_whether_warnings_block(tmp_path):
    from main import run_pipeline

    dataset = tmp_path / "warned"
    shutil.copytree(DATA, dataset)
    alerts = pd.read_csv(dataset / "alerts.csv")
    alerts.loc[0, "severity"] = "BOGUS"          # a WARNING, not an ERROR
    alerts.to_csv(dataset / "alerts.csv", index=False)

    # Default: warnings are reported but do not block.
    results = run_pipeline(str(dataset), str(tmp_path / "lenient"), CONFIG,
                            strict_validation=False)
    assert results is not None
    assert results["validation_report"]["warning_count"] >= 1

    with pytest.raises(DatasetValidationError) as excinfo:
        run_pipeline(str(dataset), str(tmp_path / "strict"), CONFIG,
                      strict_validation=True)
    assert excinfo.value.report.warnings


@needs_data
def test_blocking_errors_stop_an_assessment_regardless_of_strictness(tmp_path):
    """Strictness widens what blocks; it never narrows it."""
    from main import run_pipeline

    dataset = tmp_path / "broken"
    shutil.copytree(DATA, dataset)
    alerts = pd.read_csv(dataset / "alerts.csv")
    pd.concat([alerts, alerts.head(2)]).to_csv(
        dataset / "alerts.csv", index=False)

    for strict in (False, True):
        with pytest.raises(DatasetValidationError):
            run_pipeline(str(dataset), str(tmp_path / f"out{strict}"),
                          CONFIG, strict_validation=strict)


@needs_data
def test_report_settings_change_what_is_written(tmp_path):
    both = AssessmentService(settings=AppSettings(
        history_directory=str(tmp_path / "both"),
        generate_csv_exports=True, generate_pdf_report=True))
    _, _, _, run = both.run_assessment(DATA)
    produced = {f.name for f in run.path.iterdir()}
    assert "executive_summary.pdf" in produced
    assert "review_queue.csv" in produced

    neither = AssessmentService(settings=AppSettings(
        history_directory=str(tmp_path / "neither"),
        generate_csv_exports=False, generate_pdf_report=False))
    _, _, _, run = neither.run_assessment(DATA)
    produced = {f.name for f in run.path.iterdir()}
    assert "executive_summary.pdf" not in produced
    assert "review_queue.csv" not in produced


@needs_data
def test_the_full_results_are_always_written(tmp_path):
    """
    Everything else is derived from assessment_results.json, so no
    setting may switch it off.
    """
    service = AssessmentService(settings=AppSettings(
        history_directory=str(tmp_path / "runs"),
        generate_csv_exports=False, generate_pdf_report=False))
    _, _, _, run = service.run_assessment(DATA)
    assert run.results_path().is_file()


# ---------------------------------------------------------------------------
# The sample explainer must never become sticky user state
# ---------------------------------------------------------------------------

def test_a_persisted_mock_backend_self_heals(tmp_path):
    """
    `mock` is a TEST backend, not a deployment choice. Nothing in the
    settings UI can select or clear it — the inference path is
    deployment configuration — so a "mock" that reached the settings
    file would pin the application to sample explanations permanently,
    with no way out short of editing JSON by hand.

    This happened: a test run that did not isolate SATSA_CONFIG_DIR
    overwrote a real settings file, and the application reported
    SAMPLE EXPLANATIONS with a healthy Ollama runtime sitting right
    there unused.
    """
    from dataclasses import asdict

    service = SettingsService(path=tmp_path / "settings.json")
    service._write_section("ai", {**asdict(AIConfig()),
                                   "backend": "mock", "enabled": True})

    assert service.load_ai_config().backend == AIConfig().backend
    assert service.load_ai_config().backend != "mock"


def test_the_mock_backend_is_never_persisted(tmp_path):
    import json

    path = tmp_path / "settings.json"
    service = SettingsService(path=path)
    service.save_ai_config(AIConfig(enabled=True, backend="mock"))

    stored = json.loads(path.read_text())["ai"]["backend"]
    assert stored == AIConfig().backend
    assert stored != "mock"


def test_a_real_backend_still_round_trips(tmp_path):
    """Self-healing must not overwrite a deliberate choice."""
    service = SettingsService(path=tmp_path / "settings.json")
    for backend in ("ollama", "llamacpp"):
        service.save_ai_config(AIConfig(enabled=True, backend=backend))
        assert service.load_ai_config().backend == backend


def test_forcing_samples_still_works_through_the_environment(tmp_path,
                                                              monkeypatch):
    """
    The escape hatch that replaces it: per-process, so it cannot
    persist. This is what the test suite itself relies on.
    """
    from analytics.narration import resolve_backend_name

    service = SettingsService(path=tmp_path / "settings.json")
    service.save_ai_config(AIConfig(enabled=True, backend="ollama"))
    config = service.load_ai_config()

    monkeypatch.setenv("SATSA_NARRATION_BACKEND", "mock")
    assert resolve_backend_name(config.to_narration_config()) == "mock"
