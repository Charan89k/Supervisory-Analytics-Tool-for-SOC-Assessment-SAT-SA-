"""
What the interface says when there are no explanations.

"AI Explained 0 / 1,481" is the state a fresh install is in, and it is
the state most easily mistaken for a broken product. Three quite
different situations produce that zero — the feature is switched off,
it is on but nobody has started it, or it ran and failed — and the
count alone distinguishes none of them.

These tests hold the line that each of those says which one it is, and
that a failure says why it failed. They are deliberately about wording
and reachability rather than analytics: nothing here can change a
finding, a severity or a score.
"""

import pytest

from analytics.narration.ollama_backend import OllamaBackend
from analytics.narration.service import NarrationOutcome
from application.services.ai_config import AIConfig
from application.ui import MainWindow


class Stub:
    """
    Enough of a window to answer the caption question.

    The caption depends only on the AI configuration and whether a
    review queue exists, so the method is exercised unbound rather than
    by constructing a real window — that keeps the test independent of
    Qt and of everything else the window does.
    """

    def __init__(self, enabled=True, queue=None):
        self.ai_config = AIConfig(enabled=enabled)
        self.review_queue = queue if queue is not None else []


caption = MainWindow.explained_caption


# ----------------------------------------------------------------------
# The dashboard tile
# ----------------------------------------------------------------------

def test_a_disabled_feature_says_so_and_says_where_to_enable_it():
    text = caption(Stub(enabled=False), 0)
    assert "Off" in text
    assert "New Assessment" in text


def test_disabled_takes_precedence_over_having_no_assessment():
    """
    Switched off is the more actionable of the two.

    Telling someone to run an assessment when the feature is off sends
    them to do work that will still produce no explanation.
    """
    assert "Off" in caption(Stub(enabled=False, queue=[]), 0)


def test_no_assessment_yet_asks_for_one_rather_than_blaming_the_ai():
    text = caption(Stub(enabled=True, queue=[]), 0)
    assert "assessment" in text.lower()
    assert "Off" not in text


def test_a_ready_but_unstarted_run_names_the_control_and_its_page():
    """
    The single most important line here.

    Explanations never start on their own, and the button is on the
    Review Queue, not the dashboard the tile lives on. Without both
    names this state is indistinguishable from a broken AI.
    """
    text = caption(Stub(enabled=True, queue=[{"soc_id": "SOC-001"}]), 0)
    assert "Review Queue" in text
    assert "Explain Top Findings" in text


def test_a_completed_run_reports_the_slice_it_covered():
    """The explained count is a slice of the queue, never the whole
    assessment, and the caption must not let it read as the whole."""
    text = caption(Stub(enabled=True, queue=[{"soc_id": "SOC-001"}]), 10)
    assert "10" in text
    assert "queue" in text.lower()


@pytest.mark.parametrize("enabled,queue,explained", [
    (False, [], 0),
    (True, [], 0),
    (True, [{"soc_id": "SOC-001"}], 0),
    (True, [{"soc_id": "SOC-001"}], 10),
])
def test_every_state_produces_a_caption(enabled, queue, explained):
    """No state may fall through to an empty explanation of itself."""
    assert caption(Stub(enabled=enabled, queue=queue), explained).strip()


# ----------------------------------------------------------------------
# A failure that says why
# ----------------------------------------------------------------------

def test_a_timeout_is_recorded_with_the_model_and_the_way_out(monkeypatch):
    """
    A large model on a CPU-only machine times out on every finding,
    which looks exactly like an AI that does nothing. The backend must
    keep the reason rather than discarding the exception.
    """
    import requests

    backend = OllamaBackend({"model": "qwen2.5:14b", "timeout_seconds": 2})

    def timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "post", timeout)

    assert backend.explain({"finding_type": "SLOW_TRIAGE"}) is None
    assert "qwen2.5:14b" in backend.last_failure
    assert "2s" in backend.last_failure
    assert "smaller model" in backend.last_failure


def test_a_backend_starts_with_no_recorded_failure():
    assert OllamaBackend({"model": "qwen2.5:7b"}).last_failure == ""


def test_an_empty_response_is_a_failure_with_a_reason(monkeypatch):
    """Silence from the model is not an explanation."""
    import requests

    backend = OllamaBackend({"model": "qwen2.5:7b"})

    class Response:
        def raise_for_status(self): pass
        def json(self): return {"response": "   "}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())

    assert backend.explain({"finding_type": "SLOW_TRIAGE"}) is None
    assert "empty" in backend.last_failure.lower()


# ----------------------------------------------------------------------
# The outcome summary the supervisor actually reads
# ----------------------------------------------------------------------

class Status:
    def __init__(self, available=True, model="qwen2.5:7b"):
        self.available = available
        self.model = model
        self.is_mock = False
        self.detail = ""


def test_a_failure_summary_carries_the_reason():
    outcome = NarrationOutcome(status=Status(), considered=1)
    outcome.failed = 1
    outcome.last_error = "qwen2.5:14b did not answer within 180s."
    summary = outcome.summary()
    assert "1 failed" in summary
    assert "did not answer within 180s" in summary


def test_a_failure_without_a_known_reason_still_reads_cleanly():
    """An unexplained failure must not produce dangling punctuation."""
    outcome = NarrationOutcome(status=Status(), considered=1)
    outcome.failed = 1
    summary = outcome.summary()
    assert "1 failed" in summary
    assert "()" not in summary


def test_a_clean_run_reports_no_failure_clause():
    outcome = NarrationOutcome(status=Status(), considered=3)
    outcome.explained = 3
    assert "failed" not in outcome.summary()
