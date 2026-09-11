"""
Validation framework tests.

The framework's value depends entirely on it not flattering itself, so
these tests defend the three ways it could:

  1. By claiming expert validation it does not have.
  2. By reporting a rule as missing conditions it deliberately declines
     to fire on — which both understates the rule and creates pressure
     to remove guards that prevent false positives.
  3. By recomputing detector logic inside the validator, so the two
     drift together and agree with each other rather than with reality.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

from analytics.validation import (
    ALERT_RULES,
    ENTITY_RULES,
    GROUPED_RULES,
    UNVALIDATED_RULES,
    RuleMetrics,
    build_scopes,
    format_report,
    load_ground_truth,
    validate,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "assessment_rules.yaml")
GENERATOR = os.path.join(ROOT, "data", "generator", "generate_dataset.py")

ALL_RULES = {
    "MISSED_ESCALATION", "SLOW_TRIAGE", "FAST_CLOSURE", "MISSING_EVIDENCE",
    "REOPENED_CASE", "REPETITIVE_INVESTIGATION", "ANALYST_OVERLOAD",
    "ACK_WITHOUT_INVESTIGATION", "REPEATED_ALERT_WITHOUT_REMEDIATION",
    "TELEMETRY_GAP", "MISSING_ALERT_CATEGORY", "LOW_ACTIVITY_OUTLIER",
    "MISSING_ESCALATION_RECORDS", "MISSING_INVESTIGATIONS",
}


@pytest.fixture(scope="module")
def config():
    with open(CONFIG) as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def labelled(tmp_path_factory):
    """A small labelled dataset, assessed and validated end to end."""
    import pandas as pd

    from main import run_pipeline

    dataset = tmp_path_factory.mktemp("labelled") / "data"
    subprocess.run(
        [sys.executable, GENERATOR, "--socs", "3", "--alerts-per-soc", "200",
         "--out", str(dataset)],
        check=True, capture_output=True)

    out = tmp_path_factory.mktemp("labelled-out")
    results = run_pipeline(str(dataset), str(out), CONFIG)

    truth = load_ground_truth(str(dataset))
    alerts = pd.read_csv(dataset / "alerts.csv")
    with open(CONFIG) as handle:
        cfg = yaml.safe_load(handle)

    return validate(results, truth, str(dataset), alerts=alerts, config=cfg)


# ---------------------------------------------------------------------------
# Never claims expert validation
# ---------------------------------------------------------------------------

def test_provenance_says_synthetic(labelled):
    provenance = labelled.provenance.lower()
    assert "synthetic validation" in provenance
    assert "not against expert manual review" in provenance


def test_provenance_admits_the_circularity(labelled):
    """
    The generator injects the conditions the rules look for. A report
    that omitted this would overstate what the numbers establish.
    """
    assert "circular" in labelled.provenance.lower()


def test_report_carries_the_provenance(labelled):
    text = format_report(labelled)
    assert "SYNTHETIC VALIDATION" in text
    assert "expert" in text.lower()


def test_false_positive_meaning_is_stated(labelled):
    """
    Incidental conditions occur in generated data as they do in real
    submissions. A rule firing on one is not a defect, and reporting it
    without that context invites tuning away correct behaviour.
    """
    note = labelled.false_positive_note.lower()
    assert "not the same as the rule being wrong" in note
    assert "floor on precision" in note
    assert labelled.false_positive_note in format_report(labelled)


# ---------------------------------------------------------------------------
# Ground truth is separate from the submission
# ---------------------------------------------------------------------------

def test_ground_truth_is_not_ingested_as_a_table(tmp_path):
    """
    Labels must be invisible to the loader, or a real submission would
    differ structurally from a synthetic one.
    """
    from analytics.ingestion import resolve_dataset

    dataset = tmp_path / "data"
    subprocess.run(
        [sys.executable, GENERATOR, "--socs", "3", "--alerts-per-soc", "60",
         "--out", str(dataset)],
        check=True, capture_output=True)

    assert (dataset / "ground_truth.json").exists()
    tables = resolve_dataset(str(dataset))
    assert "ground_truth" not in tables


def test_missing_ground_truth_means_cannot_validate(tmp_path):
    """
    A real submission carries no labels. Absence must never read as
    "nothing was wrong".
    """
    assert load_ground_truth(str(tmp_path)) is None


def test_cli_refuses_to_validate_an_unlabelled_dataset(tmp_path):
    from main import run_validation
    with pytest.raises(ValueError) as excinfo:
        run_validation(str(tmp_path), str(tmp_path / "out"), CONFIG)
    assert "cannot be validated" in str(excinfo.value)
    assert "says nothing about whether it contains findings" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Every rule is accounted for
# ---------------------------------------------------------------------------

def test_every_rule_is_measured_or_explicitly_not(labelled):
    measured = {m.finding_type for m in labelled.rules}
    assert measured | UNVALIDATED_RULES == ALL_RULES


def test_unvalidated_rules_are_named_not_silently_omitted(labelled):
    assert labelled.unvalidated == sorted(UNVALIDATED_RULES)
    assert "NOT VALIDATED" in format_report(labelled)
    assert "no measurement is claimed" in format_report(labelled)


def test_rule_sets_do_not_overlap():
    assert not (ALERT_RULES & ENTITY_RULES)
    assert not (ALERT_RULES & GROUPED_RULES)
    assert not (ENTITY_RULES & GROUPED_RULES)


# ---------------------------------------------------------------------------
# Scope: a rule that declines to fire has not missed anything
# ---------------------------------------------------------------------------

def test_scopes_read_thresholds_from_the_live_config(config):
    scopes = build_scopes(config)
    severities = config["execution_gaps"]["ack_without_investigation_severities"]
    for severity in severities:
        assert severity in scopes["ACK_WITHOUT_INVESTIGATION"].description

    margin = config["execution_gaps"]["fast_closure_min_margin_minutes"]
    assert str(margin) in scopes["FAST_CLOSURE"].description


def test_in_scope_recall_is_reported_for_attribute_scoped_rules(labelled):
    """
    ACK_WITHOUT_INVESTIGATION is seeded across every severity but fires
    only on HIGH/CRITICAL. Recall against all seeded conditions
    understates it; recall within scope is the honest figure.
    """
    metrics = labelled.rule("ACK_WITHOUT_INVESTIGATION")
    assert metrics.in_scope is not None
    assert metrics.in_scope < metrics.injected
    assert metrics.out_of_scope == metrics.injected - metrics.in_scope
    assert metrics.recall_in_scope >= metrics.recall


def test_threshold_scoped_rules_are_not_silently_corrected(labelled):
    """
    Recomputing a margin or an occurrence count inside the validator
    would duplicate detector logic, and the two would drift together —
    agreeing with each other rather than with reality. Those rules name
    the threshold and report recall uncorrected.
    """
    for finding_type in ("FAST_CLOSURE", "REPETITIVE_INVESTIGATION"):
        metrics = labelled.rule(finding_type)
        assert metrics.threshold_limited is True
        assert metrics.in_scope is None
        assert metrics.recall_in_scope is None
        assert metrics.scope_note


def test_report_explains_every_scope(labelled):
    text = format_report(labelled)
    assert "RULE SCOPES" in text
    assert "are not misses" in text
    assert "not recomputed here" in text


# ---------------------------------------------------------------------------
# Metrics are arithmetic, not assertion
# ---------------------------------------------------------------------------

def test_precision_and_recall_formulas():
    metrics = RuleMetrics(finding_type="X", grain="alert",
                           true_positives=8, false_positives=2,
                           false_negatives=4)
    assert metrics.precision == pytest.approx(0.8)
    assert metrics.recall == pytest.approx(2 / 3)
    assert metrics.f1 == pytest.approx(2 * 0.8 * (2 / 3) / (0.8 + 2 / 3))


def test_metrics_with_no_observations_are_none_not_zero():
    """Zero would read as "perfectly wrong"; None reads as "not measured"."""
    metrics = RuleMetrics(finding_type="X", grain="alert")
    assert metrics.precision is None
    assert metrics.recall is None
    assert metrics.f1 is None
    assert metrics.format_ratio(None) == "—"


def test_true_positives_never_exceed_what_was_seeded(labelled):
    for metrics in labelled.rules:
        assert metrics.true_positives <= metrics.injected, metrics.finding_type
        assert metrics.true_positives + metrics.false_negatives == metrics.injected


def test_detection_is_credible_on_seeded_data(labelled):
    """
    A floor, not a target. Rules are measured against conditions built
    to trigger them, so anything far below this means something is
    broken rather than that the rules are merely imperfect.
    """
    overall = labelled.overall()
    assert overall.precision >= 0.9
    assert overall.recall >= 0.6


def test_rules_seeded_without_scope_limits_detect_everything(labelled):
    """
    SLOW_TRIAGE, MISSED_ESCALATION, REOPENED_CASE and MISSING_EVIDENCE
    have no threshold that would legitimately suppress a seeded case, so
    any miss is a real one.
    """
    for finding_type in ("SLOW_TRIAGE", "MISSED_ESCALATION",
                          "REOPENED_CASE", "MISSING_EVIDENCE"):
        metrics = labelled.rule(finding_type)
        assert metrics.false_negatives == 0, finding_type
        assert metrics.recall == pytest.approx(1.0), finding_type


# ---------------------------------------------------------------------------
# Ranking and review effort
# ---------------------------------------------------------------------------

def test_ranking_quality_is_measured(labelled):
    ranking = labelled.ranking
    assert ranking.queue_size > 0
    assert 0 <= ranking.queue_with_injected <= ranking.queue_size
    assert 0 <= ranking.precision_at_10 <= 1


def test_review_effort_states_what_it_does_not_measure(labelled):
    statement = labelled.effort.statement()
    assert "%" in statement
    assert "not whether the right things were prioritised" in statement


def test_review_effort_is_a_real_reduction(labelled):
    effort = labelled.effort
    assert effort.queue_size < effort.total_findings
    assert effort.reviewed_fraction < 0.5
