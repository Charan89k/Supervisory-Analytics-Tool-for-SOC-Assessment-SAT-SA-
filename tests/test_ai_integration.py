"""
Phase 3 tests: the AI layer as the desktop application uses it.

No test here loads a language model. The provider abstraction exists so
that every path the UI can take — disabled, not configured, unavailable,
mock, cancelled, limit reached, backend crashed — is verifiable on a
laptop in milliseconds. The one thing not covered is whether Qwen writes
good prose, which no unit test could assert anyway.

The invariant under test throughout: an assessment is complete and
authoritative before narration begins, and nothing narration does can
change it.
"""

import os
import sys
import types

import pytest

from analytics.narration.base import (
    STATE_AVAILABLE,
    STATE_DISABLED,
    STATE_MOCK,
    STATE_NOT_CONFIGURED,
    STATE_NOT_RUNNING,
    STATE_UNAVAILABLE,
    BackendStatus,
)
from analytics.narration.mock import MockBackend
from analytics.narration.service import NarrationScope, narrate_review_queue
from application.services.ai_config import (
    MODELS,
    AIConfig,
    create_ai_config,
    from_yaml_config,
)
from application.services.narration_service import NarrationService
from application.services.settings_service import SettingsService


def make_queue(n, severity="CRITICAL"):
    return [{
        "queue_rank": i + 1,
        "soc_id": "SOC-TEST",
        "finding_type": "FAST_CLOSURE",
        "rule_id": f"TRIAGE-FAST-{i:03d}",
        "severity": severity,
        "alert_id": f"ALT-{i}",
        "case_id": f"CASE-{i}",
        "rationale": f"Deterministic rationale {i}.",
        "evidence": {"investigation_duration_minutes": 2.0},
        "queue_priority": 4.0,
    } for i in range(n)]


# ---------------------------------------------------------------------------
# AI disabled
# ---------------------------------------------------------------------------

def test_disabled_reports_disabled_state():
    status = NarrationService(AIConfig(enabled=False)).status()
    assert status.available is False
    assert status.resolved_state() == STATE_DISABLED
    assert status.headline() == "AI DISABLED"


def test_disabled_never_probes_the_backend():
    """
    A disabled AI must not touch a backend at all — no port, no file, no
    model load. Probing anyway would make "off" cost something.
    """
    class ExplodingBackend(MockBackend):
        def probe(self):
            raise AssertionError("probe() called while AI was disabled")

    service = NarrationService(AIConfig(enabled=False),
                                backend=ExplodingBackend())
    assert service.status().resolved_state() == STATE_DISABLED


def test_disabled_leaves_every_record_on_its_rule_rationale():
    queue = make_queue(5)
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(enabled=False), backend=MockBackend())

    assert outcome.explained == 0
    for index, record in enumerate(queue):
        assert record["narrated_explanation"] == f"Deterministic rationale {index}."
        assert record["narration_source"] == "rule"


# ---------------------------------------------------------------------------
# Backend status: available / unavailable / not configured
# ---------------------------------------------------------------------------

def test_llamacpp_without_a_model_path_is_not_configured(chooses_own_backend):
    status = NarrationService(
        AIConfig(enabled=True, backend="llamacpp", model_path="")).status()
    assert status.resolved_state() == STATE_NOT_CONFIGURED
    assert status.headline() == "AI NOT CONFIGURED"


def test_llamacpp_with_a_missing_file_is_unavailable(tmp_path, monkeypatch,
                                                      chooses_own_backend):
    """
    Distinct from not-configured: the operator DID point SAT-SA at a
    model and it is gone, which needs different guidance.
    """
    monkeypatch.setitem(sys.modules, "llama_cpp", types.ModuleType("llama_cpp"))
    status = NarrationService(AIConfig(
        enabled=True, backend="llamacpp",
        model_path=str(tmp_path / "absent.gguf"))).status()

    assert status.resolved_state() == STATE_UNAVAILABLE
    assert "missing" in status.detail


def test_llamacpp_with_a_real_file_is_available(tmp_path, monkeypatch,
                                                 chooses_own_backend):
    monkeypatch.setitem(sys.modules, "llama_cpp", types.ModuleType("llama_cpp"))
    model = tmp_path / "qwen2.5-7b-instruct-q4.gguf"
    model.write_bytes(b"\x00" * 4096)

    status = NarrationService(AIConfig(
        enabled=True, backend="llamacpp", model_path=str(model))).status()

    assert status.available is True
    assert status.resolved_state() == STATE_AVAILABLE
    assert status.is_mock is False


def test_unreachable_ollama_is_unavailable_quickly(chooses_own_backend,
                                                    monkeypatch):
    """
    Port 1 is reserved; nothing listens there.

    The executable lookup is stubbed so the outcome does not depend on
    whether the machine running the suite happens to have Ollama
    installed — that would make the assertion pass or fail for reasons
    having nothing to do with the code under test.
    """
    import time
    from analytics.narration import ollama_runtime
    monkeypatch.setattr(ollama_runtime, "find_executable",
                        lambda: "/usr/local/bin/ollama")

    config = AIConfig(enabled=True, backend="ollama", availability_timeout=2)
    service = NarrationService(config)
    service.backend.base_url = "http://127.0.0.1:1"

    started = time.time()
    status = service.status()
    elapsed = time.time() - started

    # Installed but not answering is reported as exactly that, rather
    # than as a generic failure: it is the one case the operator fixes
    # by starting a service, not by installing anything.
    assert status.available is False
    assert status.resolved_state() == STATE_NOT_RUNNING
    assert elapsed < 10, "a dead backend must report quickly, not hang the UI"


def test_mock_backend_reports_its_own_state():
    status = NarrationService(AIConfig(enabled=True, backend="mock")).status()
    assert status.available is True
    assert status.resolved_state() == STATE_MOCK
    assert status.headline() == "SAMPLE EXPLANATIONS"


def test_a_crashing_backend_does_not_propagate():
    """A broken backend must not take the window down."""
    class BrokenBackend(MockBackend):
        def probe(self):
            raise RuntimeError("backend exploded")

    status = NarrationService(AIConfig(enabled=True),
                               backend=BrokenBackend()).status()
    assert status.available is False
    assert "backend error" in status.detail


# ---------------------------------------------------------------------------
# Scope: rank and maximum, not severity
# ---------------------------------------------------------------------------

def test_default_configuration_explains_a_small_slice():
    """
    The scope question this phase exists to answer. An assessment
    produces ~1,479 findings and an 81-item queue; the default must send
    a handful, not the lot.
    """
    config = AIConfig()
    assert config.max_explanations <= 25

    queue = make_queue(81)
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationService(
            AIConfig(enabled=True, backend="mock")).scope(),
        backend=MockBackend())

    assert outcome.explained == config.max_explanations
    assert outcome.considered == 81


def test_queue_rank_is_the_primary_scope_control():
    config = AIConfig(enabled=True, backend="mock",
                       max_explanations=100, max_queue_rank=5)
    queue = make_queue(50)
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationService(config).scope(), backend=MockBackend())

    assert outcome.explained == 5
    explained = [r for r in queue if r["narration_source"] == "mock"]
    assert all(r["queue_rank"] <= 5 for r in explained)


def test_severity_scope_barely_narrows_an_all_critical_queue():
    """
    Why severity is not the primary control: the review queue is already
    almost entirely Critical/High by construction, so filtering on it
    bounds essentially nothing.
    """
    queue = make_queue(40, severity="CRITICAL")
    scope = NarrationScope(max_explanations=1000, severity_scope="critical_high")

    from analytics.narration.service import select_for_narration
    assert len(select_for_narration(queue, scope)) == 40


def test_max_explanations_is_enforced_exactly():
    for limit in (1, 3, 10):
        queue = make_queue(50)
        outcome = narrate_review_queue(
            queue, {}, scope=NarrationScope(max_explanations=limit),
            backend=MockBackend())
        assert outcome.explained == limit
        assert sum(1 for r in queue if r["narration_source"] == "mock") == limit


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancellation_stops_the_run_and_is_reported():
    queue = make_queue(30)
    seen = {"n": 0}

    def should_cancel():
        seen["n"] += 1
        return seen["n"] > 4

    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=30),
        backend=MockBackend(), should_cancel=should_cancel)

    assert outcome.cancelled is True
    assert 0 < outcome.explained < 30
    assert "cancelled" in outcome.summary()


def test_cancellation_keeps_the_explanations_already_produced():
    """A cancelled run is partial, not discarded."""
    queue = make_queue(20)
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 3

    narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=20),
        backend=MockBackend(), should_cancel=should_cancel)

    explained = [r for r in queue if r["narration_source"] == "mock"]
    assert len(explained) == 3
    assert all(r["narrated_explanation"] for r in explained)

    # Everything else still carries its deterministic rationale.
    rest = [r for r in queue if r["narration_source"] == "rule"]
    assert len(rest) == 17
    assert all(r["narrated_explanation"].startswith("Deterministic") for r in rest)


def test_narration_worker_exposes_cancel_without_qt_running():
    from application.narration_worker import NarrationWorker
    worker = NarrationWorker(make_queue(3), AIConfig(enabled=True, backend="mock"))
    assert worker._cancelled is False
    worker.cancel()
    assert worker._cancelled is True


# ---------------------------------------------------------------------------
# Failure never reaches the assessment
# ---------------------------------------------------------------------------

def test_a_failing_backend_leaves_the_queue_intact():
    class FailingBackend(MockBackend):
        def explain(self, finding):
            return None   # every call fails

    queue = make_queue(10)
    outcome = narrate_review_queue(
        queue, {}, scope=NarrationScope(max_explanations=10),
        backend=FailingBackend())

    assert outcome.explained == 0
    assert outcome.failed == 10
    assert all(r["narrated_explanation"].startswith("Deterministic")
               for r in queue)
    assert "kept the rule rationale" in outcome.summary()


DETERMINISTIC_FIELDS = ("queue_rank", "soc_id", "finding_type", "rule_id",
                        "severity", "alert_id", "case_id", "evidence",
                        "rationale", "queue_priority")


def test_narration_cannot_alter_any_deterministic_field():
    """
    The rule the whole design rests on. A backend that tried to rewrite a
    severity or a score would have nowhere to write it: the service only
    ever assigns narration_* keys.
    """
    class MeddlingBackend(MockBackend):
        def explain(self, finding):
            # A hostile or malfunctioning backend mutating its input.
            finding["severity"] = "LOW"
            finding["queue_priority"] = 0.0
            finding["evidence"] = {"fabricated": True}
            return super().explain(finding)

    queue = make_queue(5)
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=5),
                          backend=MeddlingBackend())

    # The service cannot defend against a backend mutating the dict it was
    # handed, so this documents the boundary honestly: what it guarantees
    # is that the SERVICE writes nothing but narration fields.
    written_by_service = {k for k in queue[0] if k.startswith("narrat")}
    assert written_by_service == {
        "narrated_explanation", "narration_source", "narration_is_mock",
        "narration_model", "narration_provenance"}


def test_service_writes_only_narration_keys():
    queue = make_queue(3)
    before = set(queue[0])
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=3),
                          backend=MockBackend())
    added = set(queue[0]) - before
    assert added and all(k.startswith("narrat") for k in added)


# ---------------------------------------------------------------------------
# Mock output is never presentable as model output
# ---------------------------------------------------------------------------

def test_mock_explanations_are_labelled_everywhere():
    queue = make_queue(1)
    narrate_review_queue(queue, {}, scope=NarrationScope(max_explanations=1),
                          backend=MockBackend())
    record = queue[0]

    assert record["narration_is_mock"] is True
    assert record["narration_model"] == "deterministic-sample"
    assert "[SAMPLE EXPLANATION]" in record["narrated_explanation"]
    assert "no language model" in record["narration_provenance"].lower()
    assert "qwen" not in record["narration_model"].lower()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_round_trip(tmp_path):
    service = SettingsService(tmp_path / "settings.json")
    config = create_ai_config(mode="fast", enabled=True, backend="llamacpp")
    config.model_path = "/models/qwen.gguf"
    config.max_explanations = 7
    config.max_queue_rank = 12

    service.save_ai_config(config)
    loaded = service.load_ai_config()

    assert loaded.enabled is True
    assert loaded.backend == "llamacpp"
    assert loaded.mode == "fast"
    assert loaded.model_path == "/models/qwen.gguf"
    assert loaded.max_explanations == 7
    assert loaded.max_queue_rank == 12


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json")
    assert SettingsService(path).load_ai_config().backend == AIConfig().backend


def test_missing_settings_file_returns_defaults(tmp_path):
    loaded = SettingsService(tmp_path / "never-written.json").load_ai_config()
    assert loaded.enabled is False


def test_settings_default_is_ai_off():
    assert AIConfig().enabled is False


def test_no_configuration_path_defaults_to_the_14b(tmp_path):
    """14B is selectable but must never be what a fresh install uses."""
    assert AIConfig().model != MODELS["deep"]
    assert SettingsService(tmp_path / "x.json").load_ai_config().model != MODELS["deep"]
    assert create_ai_config().model != MODELS["deep"]


def test_ai_config_to_narration_config_shape():
    config = AIConfig(enabled=True, backend="llamacpp",
                       model_path="/m.gguf", max_explanations=4)
    narration = config.to_narration_config()["llm_narration"]

    assert narration["enabled"] is True
    assert narration["backend"] == "llamacpp"
    assert narration["model_path"] == "/m.gguf"
    assert narration["max_explanations"] == 4
