"""
Demonstration mode tests.

The property that matters: demonstration mode must run the REAL
pipeline. A demonstration that took a different path from the product
would not be evidence that the product works — it would only be
evidence that the demonstration works.

The second property: demonstration data must be unmistakable. A judge,
an evaluator, or a supervisor must never be able to take a screenshot of
synthetic records for a real entity's assessment.
"""

import json
import os
import subprocess
import sys

import pytest

from application.services.demo_service import (
    DEMO_ALERTS_PER_ENTITY,
    DEMO_BANNER,
    DEMO_ENTITIES,
    DEMO_LABEL,
    DEMO_SEED,
    DemoService,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")

ALL_RULES = {
    "MISSED_ESCALATION", "SLOW_TRIAGE", "FAST_CLOSURE", "MISSING_EVIDENCE",
    "REOPENED_CASE", "REPETITIVE_INVESTIGATION", "ANALYST_OVERLOAD",
    "ACK_WITHOUT_INVESTIGATION", "REPEATED_ALERT_WITHOUT_REMEDIATION",
    "TELEMETRY_GAP", "MISSING_ALERT_CATEGORY", "LOW_ACTIVITY_OUTLIER",
    "MISSING_ESCALATION_RECORDS", "MISSING_INVESTIGATIONS",
}


@pytest.fixture(scope="module")
def demo_dataset(tmp_path_factory):
    """A demonstration dataset generated into a temporary project root."""
    root = tmp_path_factory.mktemp("demo-root")
    (root / "data").mkdir()
    os.symlink(os.path.join(ROOT, "data", "generator"),
               root / "data" / "generator")
    service = DemoService(project_root=root)
    return service, service.prepare()


@pytest.fixture(scope="module")
def demo_assessment(demo_dataset, tmp_path_factory):
    """The demonstration dataset, assessed by the real pipeline."""
    from main import run_pipeline

    _, dataset = demo_dataset
    out = tmp_path_factory.mktemp("demo-out")
    return run_pipeline(str(dataset.path), str(out), CONFIG,
                         export_csv=True, export_pdf=True), out


# ---------------------------------------------------------------------------
# It is the real pipeline
# ---------------------------------------------------------------------------

def test_demo_dataset_is_a_real_dataset(demo_dataset):
    """
    Ingested by the ordinary adapter, with no special case. If this
    needed its own loader it would not be demonstrating the product.

    The alert count is below entities x alerts-per-entity because the
    low_activity profile deliberately produces a fraction of the volume —
    that sparseness is what LOW_ACTIVITY_OUTLIER exists to detect.
    """
    from analytics.ingestion import resolve_dataset

    _, dataset = demo_dataset
    tables = resolve_dataset(str(dataset.path))

    assert len(tables) == 13
    assert tables["socs"].shape[0] == DEMO_ENTITIES
    assert 0 < len(tables["alerts"]) <= DEMO_ENTITIES * DEMO_ALERTS_PER_ENTITY
    for table in ("cases", "alert_events", "escalations", "telemetry"):
        assert not tables[table].empty, table


def test_demo_dataset_passes_the_ordinary_validator(demo_dataset):
    from main import validate_dataset_at

    _, dataset = demo_dataset
    report = validate_dataset_at(str(dataset.path))
    assert not report.has_errors


def test_demo_runs_the_real_pipeline(demo_assessment):
    results, _ = demo_assessment
    assert results["run_metadata"]["entities_assessed"] == DEMO_ENTITIES
    assert results["run_metadata"]["total_findings"] > 0
    assert results["review_queue"]


def test_no_hardcoded_findings_anywhere_in_demo_mode():
    """
    The service must prepare a dataset and nothing else. A canned
    finding, entity or score would make the demonstration a fiction.

    Scans the parsed CODE rather than the file text, so prose in a
    docstring that happens to mention "evidence" does not trip it while a
    real literal would.
    """
    import ast
    import inspect

    from application.services import demo_service

    tree = ast.parse(inspect.getsource(demo_service))
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    # Drop docstrings — they are prose about the module, not data.
    docstrings = {ast.get_docstring(n) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef))}
    data_literals = [v for v in literals if v not in docstrings]

    for forbidden in ("finding_type", "rule_id", "supervisory_risk_score",
                       "rationale", "CRITICAL", "SLOW_TRIAGE"):
        offenders = [v for v in data_literals if forbidden in v]
        assert not offenders, f"{forbidden}: {offenders}"


# ---------------------------------------------------------------------------
# It demonstrates the product's capabilities
# ---------------------------------------------------------------------------

def test_every_rule_fires_on_the_demo_dataset(demo_assessment):
    """A demonstration that exercises 11 of 14 rules undersells the tool."""
    results, _ = demo_assessment
    fired = set()
    for entity in results["entities"]:
        for finding in (entity["execution_gap_findings"]
                        + entity["negative_space_findings"]):
            fired.add(finding["finding_type"])
    assert ALL_RULES - fired == set(), f"never fired: {ALL_RULES - fired}"


def test_both_categories_are_represented(demo_assessment):
    results, _ = demo_assessment
    execution = sum(len(e["execution_gap_findings"])
                    for e in results["entities"])
    negative = sum(len(e["negative_space_findings"])
                   for e in results["entities"])
    assert execution > 0 and negative > 0


def test_peer_benchmarking_is_demonstrable(demo_assessment):
    """
    Nine entities gives three sector groups of three, the minimum for a
    peer comparison to say anything. With fewer, benchmarking correctly
    withholds itself and the demonstration shows nothing.
    """
    from analytics.benchmarking import build_peer_groups

    results, _ = demo_assessment
    groups = build_peer_groups(results)
    assert all(group.comparable for group in groups.values())
    assert len(groups) >= 3


def test_capability_mapping_is_demonstrable(demo_assessment):
    import yaml

    from analytics import capabilities

    results, _ = demo_assessment
    with open(CONFIG) as handle:
        config = yaml.safe_load(handle)

    areas = capabilities.assess_entity(results["entities"][0], config)
    assert len(areas) == 8
    assert any(area.finding_count > 0 for area in areas)


def test_review_queue_correlates_cases(demo_assessment):
    from application.services.review_service import build_cases

    results, _ = demo_assessment
    cases = build_cases(results["review_queue"])
    assert cases
    assert any(case.finding_count > 1 for case in cases)


def test_evidence_traces_to_source_records(demo_dataset, demo_assessment):
    from analytics.evidence import evidence_bundle_for_finding
    from analytics.loader import load_soc_dataset
    from analytics.normalizer import build_alerts_enriched, normalize_dataset

    _, dataset = demo_dataset
    results, _ = demo_assessment

    data = normalize_dataset(load_soc_dataset(str(dataset.path)))
    enriched = build_alerts_enriched(data)
    finding = results["entities"][0]["execution_gap_findings"][0]

    bundle = evidence_bundle_for_finding(data, enriched, finding)
    assert bundle.get("_grain")
    assert {k: v for k, v in bundle.items() if k != "_grain"}


def test_reports_are_produced(demo_assessment):
    _, out = demo_assessment
    produced = {f.name for f in out.iterdir() if f.is_file()}
    assert "executive_summary.pdf" in produced
    assert "assessment_results.json" in produced
    assert "review_queue.csv" in produced


# ---------------------------------------------------------------------------
# It is reproducible and fast
# ---------------------------------------------------------------------------

def test_the_seed_is_fixed():
    """
    A demonstration must show the same entities, findings and ranking
    every time — including on a judge's machine.
    """
    assert isinstance(DEMO_SEED, int)


def test_the_same_seed_produces_the_same_assessment(tmp_path):
    """
    A demonstration must show the same entities, findings and ranking
    every time — including on a judge's machine.

    Asserted on the ASSESSMENT rather than on the raw rows.
    `timestamp_created` is anchored to wall-clock time so a demonstration
    dataset always looks recent, which means two generations seconds
    apart differ in that one column. Everything the assessment depends on
    — severities, assignments, relative timings, injected conditions — is
    seeded, so the outcome is identical.
    """
    import io
    import contextlib

    import pandas as pd

    from main import run_pipeline

    generator = os.path.join(ROOT, "data", "generator",
                             "generate_dataset.py")
    outcomes, frames = [], []

    for run in ("a", "b"):
        out = tmp_path / run
        subprocess.run(
            [sys.executable, generator, "--socs", "3",
             "--alerts-per-soc", "80", "--seed", str(DEMO_SEED),
             "--out", str(out)], check=True, capture_output=True)
        frames.append(pd.read_csv(out / "alerts.csv"))

        with contextlib.redirect_stdout(io.StringIO()):
            results = run_pipeline(str(out), str(tmp_path / f"{run}-out"),
                                    CONFIG)
        outcomes.append([
            (e["soc_id"], e["priority_rank"],
             round(e["supervisory_risk_score"], 4),
             len(e["execution_gap_findings"]),
             len(e["negative_space_findings"]))
            for e in results["entities"]
        ])

    assert outcomes[0] == outcomes[1]

    # Every column except the wall-clock-anchored timestamp is identical.
    differing = [c for c in frames[0].columns
                 if not frames[0][c].equals(frames[1][c])]
    assert differing == ["timestamp_created"], differing


def test_preparing_twice_does_not_regenerate(demo_dataset):
    """A second demonstration must start instantly."""
    service, _ = demo_dataset
    assert service.is_ready()
    assert service.prepare().generated is False


def test_generation_failure_is_reported_not_silent(tmp_path):
    """
    A half-written folder must not be left to be mistaken for a
    dataset.
    """
    service = DemoService(project_root=tmp_path)   # no generator present
    with pytest.raises(RuntimeError, match="generator is missing"):
        service.prepare()


# ---------------------------------------------------------------------------
# It is unmistakably synthetic
# ---------------------------------------------------------------------------

def test_the_banner_says_it_is_not_a_real_entity():
    lowered = DEMO_BANNER.lower()
    assert "demonstration data" in lowered
    assert "synthetic" in lowered
    assert "not a real critical sector entity" in lowered
    assert "not derived from any real submission" in lowered


def test_the_dataset_label_says_synthetic():
    assert "synthetic" in DEMO_LABEL.lower()
