"""
Shared fixtures.

The assessment fixtures below run the pipeline into a temporary
directory rather than reading `outputs/`. That directory is a mutable
work area — the desktop application writes to it on every run, and a
multi-period run replaces its contents with the newest period's
assessment. Tests that read it pass or fail depending on what was run
last, which is not a property a test suite should have.

Session-scoped, so the pipeline runs once for the whole suite.
"""

import json
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _force_mock_narration_backend():
    """
    No automated run may load a language model.

    The documented guarantee has always been that the suite runs with
    SATSA_NARRATION_BACKEND=mock; until now nothing enforced it, and it
    held only because the configured default happened to be the mock
    backend. The default is now a real runtime, so the guarantee is
    asserted here instead of being a side effect. Tests that exercise
    backend resolution itself clear the variable with monkeypatch.
    """
    os.environ["SATSA_NARRATION_BACKEND"] = "mock"


@pytest.fixture
def chooses_own_backend(monkeypatch):
    """
    For tests that deliberately exercise one specific backend.

    The session-wide forcing above is a safety net against a real model
    being loaded by accident. A test that names its backend is not an
    accident, so it opts out explicitly — and says so by requesting
    this fixture, which keeps the intent visible at the call site.
    """
    monkeypatch.delenv("SATSA_NARRATION_BACKEND", raising=False)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "synthetic")
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")


@pytest.fixture(scope="session")
def assessment(tmp_path_factory):
    """
    One full assessment of the synthetic dataset, in a private
    directory. Skips rather than fails when the dataset has not been
    generated, since the dataset is not checked in.
    """
    if not os.path.isdir(DATA):
        pytest.skip("synthetic dataset not generated")

    from main import run_pipeline

    out = tmp_path_factory.mktemp("assessment")
    results = run_pipeline(DATA, str(out), CONFIG,
                            export_csv=True, export_pdf=True)
    return results


@pytest.fixture(scope="session")
def assessment_dir(assessment, tmp_path_factory):
    """The directory the session assessment was written to."""
    return assessment


@pytest.fixture(scope="session")
def review_queue(assessment):
    return assessment["review_queue"]
