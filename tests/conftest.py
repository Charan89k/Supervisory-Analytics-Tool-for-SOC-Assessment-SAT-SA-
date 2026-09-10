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
