"""
Application lifecycle and shell tests.

These cover the state model the later phases build on, plus the pieces
of the desktop shell that have a testable contract: settings
persistence, page gating, and the narration boundary.

Nothing here needs a QApplication except the widget-level checks, which
run under the offscreen platform plugin.
"""

import json
import os

import pytest

from analytics.narration.mock import MockBackend
from analytics.narration.service import (
    NARRATION_INPUT_FIELDS,
    NarrationScope,
    finding_view,
    narrate_review_queue,
)
from application.services.ai_config import AIConfig, create_ai_config
from application.services.settings_service import SettingsService
from application.session import GUIDANCE, STATUS_LABELS, Phase, SessionState
from application.version import APP_NAME, APP_VERSION


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_session_starts_with_no_dataset():
    session = SessionState()
    assert session.phase is Phase.NO_DATASET
    assert session.has_results is False
    assert session.can_run_assessment is False
    assert session.is_busy is False


def test_the_documented_happy_path():
    """Launch -> validation -> ready -> assessing -> loaded."""
    session = SessionState()
    seen = []
    session.changed.connect(lambda phase: seen.append(phase))

    for phase in (Phase.VALIDATING, Phase.READY, Phase.ASSESSING, Phase.LOADED):
        session.transition(phase)

    assert seen == [Phase.VALIDATING, Phase.READY, Phase.ASSESSING, Phase.LOADED]
    assert session.has_results is True


def test_transition_to_the_same_phase_does_not_re_emit():
    """Otherwise every no-op transition re-runs page gating."""
    session = SessionState()
    seen = []
    session.changed.connect(lambda phase: seen.append(phase))

    session.transition(Phase.READY)
    session.transition(Phase.READY)
    session.transition(Phase.READY)

    assert seen == [Phase.READY]


def test_results_are_only_available_in_loaded():
    session = SessionState()
    for phase in Phase:
        session.transition(phase)
        assert session.has_results is (phase is Phase.LOADED), phase


def test_assessment_can_be_re_run_from_loaded():
    """Running a second assessment must not require reselecting a dataset."""
    session = SessionState()
    session.transition(Phase.LOADED)
    assert session.can_run_assessment is True


def test_invalid_dataset_blocks_running():
    session = SessionState()
    session.transition(Phase.INVALID)
    assert session.can_run_assessment is False
    assert session.has_results is False


def test_busy_phases_are_the_ones_with_work_in_flight():
    session = SessionState()
    busy = set()
    for phase in Phase:
        session.transition(phase)
        if session.is_busy:
            busy.add(phase)
    assert busy == {Phase.VALIDATING, Phase.ASSESSING}


def test_every_phase_has_guidance_and_a_status_label():
    """A blank page must always be able to explain itself."""
    for phase in Phase:
        assert GUIDANCE.get(phase), phase
        assert STATUS_LABELS.get(phase), phase


def test_dataset_is_recorded_on_the_session():
    session = SessionState()
    session.set_dataset("/submissions/2026Q3", "2026Q3")
    assert session.dataset_path == "/submissions/2026Q3"
    assert session.dataset_label == "2026Q3"


# ---------------------------------------------------------------------------
# Narration boundary (hardened in Phase 4)
# ---------------------------------------------------------------------------

def _finding():
    return {
        "queue_rank": 1,
        "soc_id": "SOC-001",
        "finding_type": "FAST_CLOSURE",
        "rule_id": "TRIAGE-FAST-001",
        "severity": "CRITICAL",
        "rationale": "Deterministic rationale.",
        "evidence": {"investigation_duration_minutes": 2.0,
                      "nested": {"value": 1}},
        "queue_priority": 4.0,
    }


class Vandal(MockBackend):
    """A backend that rewrites everything it is handed."""

    def explain(self, finding):
        finding["severity"] = "LOW"
        finding["queue_priority"] = 0.0
        finding["rule_id"] = "FABRICATED-001"
        finding["evidence"]["fabricated"] = True
        finding["evidence"]["nested"]["value"] = 999
        return super().explain(finding)


def test_a_backend_cannot_alter_the_authoritative_record():
    queue = [_finding()]
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=1),
                          backend=Vandal())
    record = queue[0]

    assert record["severity"] == "CRITICAL"
    assert record["queue_priority"] == 4.0
    assert record["rule_id"] == "TRIAGE-FAST-001"
    assert "fabricated" not in record["evidence"]
    assert record["evidence"]["nested"]["value"] == 1


def test_the_explanation_is_still_written_despite_the_mutation():
    """Hardening must not break the feature it protects."""
    queue = [_finding()]
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=1), backend=Vandal())

    assert outcome.explained == 1
    assert queue[0]["narration_source"] == "mock"
    assert queue[0]["narrated_explanation"]


def test_finding_view_is_a_deep_copy():
    record = _finding()
    view = finding_view(record)

    view["evidence"]["nested"]["value"] = 42
    assert record["evidence"]["nested"]["value"] == 1


def test_finding_view_carries_what_an_explanation_needs():
    view = finding_view(_finding())
    for field in ("finding_type", "rule_id", "severity", "rationale",
                   "evidence", "soc_id"):
        assert field in view, field


def test_finding_view_excludes_prior_narration():
    """A backend must not see, or be able to echo back, earlier output."""
    record = _finding()
    record["narrated_explanation"] = "an earlier explanation"
    record["narration_source"] = "mock"

    view = finding_view(record)
    assert not any(key.startswith("narrat") for key in view)
    assert set(view) <= set(NARRATION_INPUT_FIELDS)


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

def test_window_state_round_trip(tmp_path):
    service = SettingsService(tmp_path / "settings.json")
    service.save_window_state("R0VPTQ==", "U1RBVEU=", 3)

    stored = service.load_window_state()
    assert stored["geometry"] == "R0VPTQ=="
    assert stored["page_index"] == 3


def test_window_and_ai_sections_coexist(tmp_path):
    """Saving one section must not discard the other."""
    path = tmp_path / "settings.json"
    service = SettingsService(path)

    service.save_ai_config(create_ai_config(enabled=True, backend="mock"))
    service.save_window_state("GEO", "ST", 2)

    assert service.load_ai_config().enabled is True
    assert service.load_window_state()["page_index"] == 2
    assert set(json.loads(path.read_text())) == {"ai", "window"}


def test_missing_window_state_is_an_empty_dict(tmp_path):
    assert SettingsService(tmp_path / "none.json").load_window_state() == {}


def test_corrupt_settings_do_not_break_window_state(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{{{ not json")
    assert SettingsService(path).load_window_state() == {}


def test_settings_write_is_atomic(tmp_path):
    """No .tmp file is left behind after a save."""
    path = tmp_path / "settings.json"
    service = SettingsService(path)
    service.save_ai_config(AIConfig())

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_version_is_a_three_part_string():
    parts = APP_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_app_name_is_used_consistently():
    from application import version
    assert APP_NAME == "SAT-SA"
    assert APP_NAME in version.APP_FULL_NAME


def test_tagline_states_what_the_tool_is_not():
    """
    The product description must not let a reader mistake a supervisory
    analytics tool for an operational security capability.
    """
    from application.version import APP_TAGLINE
    lowered = APP_TAGLINE.lower()
    assert "does not replace" in lowered
    assert "offline" in lowered
