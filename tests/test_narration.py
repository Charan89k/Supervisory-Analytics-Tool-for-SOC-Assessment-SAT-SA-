"""
Tests for the optional local explanation layer.

None of these load a language model. The point of the backend
abstraction is that every path through the narration layer — disabled,
unavailable, mock, and real-backend configuration — can be verified
without spending minutes of CPU and gigabytes of RAM on inference.
The one thing not tested here is whether Qwen produces good prose,
which is not a property a unit test can assert anyway.

The invariant every test below is really defending: the deterministic
assessment does not depend on any of this.
"""

import os

import pandas as pd
import pytest
import yaml

from analytics.narration import (
    BACKENDS,
    DEFAULT_BACKEND,
    LlamaCppBackend,
    MockBackend,
    OllamaBackend,
    create_backend,
    resolve_backend_name,
)
from analytics.narration.service import (
    NarrationScope,
    narrate_review_queue,
    select_for_narration,
)
from analytics.llm_narration import narrate_finding, narrate_queue

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..",
                           "config", "assessment_rules.yaml")

RULE_RATIONALE = "Rule-generated rationale that must survive every failure path."


@pytest.fixture(scope="module")
def cfg():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def make_finding(**overrides):
    base = {
        "finding_type": "FAST_CLOSURE",
        "rule_id": "TRIAGE-FAST-001",
        "soc_id": "SOC-TEST",
        "severity": "CRITICAL",
        "rationale": RULE_RATIONALE,
        "evidence": {"investigation_duration_minutes": 2.0},
        "queue_rank": 1,
    }
    base.update(overrides)
    return base


def make_queue(n, severity="CRITICAL"):
    return [make_finding(queue_rank=i + 1, severity=severity,
                         rule_id=f"RULE-{i}") for i in range(n)]


# ---------------------------------------------------------------------------
# 1. AI disabled
# ---------------------------------------------------------------------------

def test_disabled_narration_returns_rule_rationale(cfg):
    disabled = dict(cfg)
    disabled["llm_narration"] = {**cfg["llm_narration"], "enabled": False}
    assert narrate_finding(make_finding(), disabled) == RULE_RATIONALE


def test_disabled_narration_still_populates_every_record(cfg):
    """
    Downstream consumers must never have to handle a missing field, so
    even a fully disabled run leaves every record with an explanation —
    the deterministic one.
    """
    queue = make_queue(5)
    result = narrate_review_queue(
        queue, cfg, scope=NarrationScope(enabled=False),
        backend=MockBackend())

    assert result.explained == 0
    assert all(r["narrated_explanation"] == RULE_RATIONALE for r in queue)
    assert all(r["narration_source"] == "rule" for r in queue)
    assert all(r["narration_is_mock"] is False for r in queue)


# ---------------------------------------------------------------------------
# 2. AI backend unavailable
# ---------------------------------------------------------------------------

def test_unavailable_ollama_reports_unavailable_not_crash(monkeypatch):
    """
    Port 1 is reserved and nothing listens on it — a dead backend.

    find_executable is stubbed so this asserts the probe's behaviour
    rather than the contents of the developer's PATH.
    """
    from analytics.narration import ollama_runtime
    monkeypatch.setattr(ollama_runtime, "find_executable",
                        lambda: "/usr/local/bin/ollama")

    backend = OllamaBackend({
        "base_url": "http://127.0.0.1:1",
        "model": "qwen2.5:7b",
        "availability_timeout_seconds": 1,
    })
    status = backend.probe()
    assert status.available is False
    assert "127.0.0.1:1" in status.detail
    assert "AI NOT RUNNING" in status.label()
    assert status.remedy()


def test_unavailable_backend_explain_returns_none_without_raising():
    backend = OllamaBackend({
        "base_url": "http://127.0.0.1:1",
        "model": "qwen2.5:7b",
        "timeout_seconds": 1,
    })
    assert backend.explain(make_finding()) is None


def test_unavailable_backend_leaves_queue_on_rule_rationale(cfg):
    queue = make_queue(3)
    backend = OllamaBackend({
        "base_url": "http://127.0.0.1:1",
        "availability_timeout_seconds": 1,
    })
    outcome = narrate_review_queue(queue, cfg, scope=NarrationScope(),
                                    backend=backend)

    assert outcome.explained == 0
    assert outcome.status.available is False
    assert "AI unavailable" in outcome.summary()
    assert all(r["narrated_explanation"] == RULE_RATIONALE for r in queue)


def test_llamacpp_missing_model_file_is_unavailable(tmp_path):
    backend = LlamaCppBackend({"model_path": str(tmp_path / "absent.gguf")})
    status = backend.probe()
    assert status.available is False
    assert status.detail  # always explains itself


def test_llamacpp_unconfigured_path_is_unavailable():
    status = LlamaCppBackend({}).probe()
    assert status.available is False


# ---------------------------------------------------------------------------
# 3. Mock AI enabled
# ---------------------------------------------------------------------------

def test_mock_backend_is_always_available():
    status = MockBackend().probe()
    assert status.available is True
    assert status.is_mock is True


def test_mock_explanation_never_claims_to_be_a_language_model():
    """
    A supervisory finding's explanation must not misrepresent how it was
    produced. Sample text is labelled in the text itself, in the result
    metadata, and in the provenance string.
    """
    result = MockBackend().explain(make_finding())
    assert result.is_mock is True
    assert result.model == "deterministic-sample"
    assert "qwen" not in result.model.lower()
    assert "[SAMPLE EXPLANATION]" in result.text
    assert "no language model" in result.provenance().lower()


def test_mock_explanation_is_deterministic():
    finding = make_finding()
    assert MockBackend().explain(finding).text == MockBackend().explain(finding).text


def test_mock_explanation_is_grounded_in_the_finding():
    """It restates the rule's own output; it does not add new claims."""
    result = MockBackend().explain(make_finding())
    assert RULE_RATIONALE in result.text
    assert "TRIAGE-FAST-001" in result.text
    assert "SOC-TEST" in result.text
    assert "investigation duration minutes = 2.0" in result.text


def test_mock_narration_marks_provenance_on_the_record(cfg):
    queue = make_queue(3)
    outcome = narrate_review_queue(
        queue, cfg, scope=NarrationScope(max_explanations=2),
        backend=MockBackend())

    assert outcome.explained == 2
    assert queue[0]["narration_is_mock"] is True
    assert queue[0]["narration_source"] == "mock"
    assert queue[2]["narration_source"] == "rule", "beyond the limit"


# ---------------------------------------------------------------------------
# 4. Real backend configuration
# ---------------------------------------------------------------------------

def test_backend_registry_exposes_all_three_paths():
    assert set(BACKENDS) == {"mock", "ollama", "llamacpp"}


def test_config_selects_backend(cfg, monkeypatch):
    # This test is about CONFIG resolution, so the environment override
    # must be cleared first. Otherwise the test passes or fails on
    # whether the developer happened to export SATSA_NARRATION_BACKEND.
    monkeypatch.delenv("SATSA_NARRATION_BACKEND", raising=False)

    for name, expected in [("ollama", OllamaBackend),
                            ("llamacpp", LlamaCppBackend),
                            ("mock", MockBackend)]:
        scoped = {"llm_narration": {**cfg["llm_narration"], "backend": name}}
        assert isinstance(create_backend(scoped), expected)


def test_environment_override_beats_config(cfg, monkeypatch):
    """
    The lever that keeps automated runs off a real model regardless of
    what the config file says.
    """
    monkeypatch.delenv("SATSA_NARRATION_BACKEND", raising=False)
    scoped = {"llm_narration": {**cfg["llm_narration"], "backend": "ollama"}}
    assert resolve_backend_name(scoped) == "ollama"

    monkeypatch.setenv("SATSA_NARRATION_BACKEND", "mock")
    assert resolve_backend_name(scoped) == "mock"
    assert isinstance(create_backend(scoped), MockBackend)


def test_unknown_backend_falls_back_without_raising(cfg, monkeypatch):
    """
    A misspelled backend name resolves to the sample explainer, which
    labels its own output — never to the real default, which would run
    a model the operator did not ask for and hide the typo.
    """
    monkeypatch.delenv("SATSA_NARRATION_BACKEND", raising=False)
    scoped = {"llm_narration": {**cfg["llm_narration"], "backend": "typo"}}
    assert isinstance(create_backend(scoped), MockBackend)


def test_absent_backend_setting_resolves_to_the_zero_config_default(
        cfg, monkeypatch):
    """
    The other half of the rule above: nothing configured is not an
    error, it is the shipped default, and it must not need an entry in
    a config file to work.
    """
    monkeypatch.delenv("SATSA_NARRATION_BACKEND", raising=False)
    scoped = {"llm_narration": {k: v for k, v in cfg["llm_narration"].items()
                                 if k != "backend"}}
    assert resolve_backend_name(scoped) == DEFAULT_BACKEND
    assert DEFAULT_BACKEND == "ollama"


def test_llamacpp_reads_its_real_configuration():
    backend = LlamaCppBackend({
        "model_path": "/models/qwen2.5-7b-instruct-q4_k_m.gguf",
        "context_size": 8192,
        "max_tokens": 512,
        "temperature": 0.2,
        "threads": 4,
    })
    assert backend.model_path == "/models/qwen2.5-7b-instruct-q4_k_m.gguf"
    assert backend.context_size == 8192
    assert backend.max_tokens == 512
    assert backend.temperature == 0.2
    assert backend.threads == 4
    assert backend.name == "llamacpp"


def test_shipped_default_model_is_not_the_14b(cfg):
    """
    14B needs ~9 GB resident and is unusable on the documented
    development hardware. A default that hangs the machine it ships on
    is not a default.
    """
    assert cfg["llm_narration"]["model"] != "qwen2.5:14b"


def test_narration_is_disabled_by_default(cfg):
    assert cfg["llm_narration"]["enabled"] is False


def test_probe_timeout_is_far_shorter_than_generation_timeout(cfg):
    """A dead backend must report itself dead in about a second."""
    narration = cfg["llm_narration"]
    assert narration["availability_timeout_seconds"] <= 5
    assert narration["availability_timeout_seconds"] < narration["timeout_seconds"]


# ---------------------------------------------------------------------------
# 5. Scope control — the queue must not be sent wholesale to a model
# ---------------------------------------------------------------------------

def test_scope_limits_the_number_of_explanations():
    selected = select_for_narration(make_queue(80), NarrationScope(max_explanations=10))
    assert len(selected) == 10


def test_scope_respects_queue_rank_cutoff():
    selected = select_for_narration(
        make_queue(80), NarrationScope(max_explanations=100, max_queue_rank=5))
    assert len(selected) == 5
    assert all(r["queue_rank"] <= 5 for r in selected)


def test_scope_filters_by_severity():
    queue = make_queue(4, severity="CRITICAL") + make_queue(4, severity="MEDIUM")
    selected = select_for_narration(
        queue, NarrationScope(max_explanations=100, severity_scope="critical"))
    assert len(selected) == 4
    assert all(r["severity"] == "CRITICAL" for r in selected)


def test_default_scope_is_small_enough_for_a_laptop(cfg):
    """
    At 1-4 minutes per call on CPU, the default must be minutes of work,
    not hours. The full finding set is ~1,480 items.
    """
    assert cfg["llm_narration"]["max_explanations"] <= 25


def test_narration_can_be_cancelled_mid_run(cfg):
    queue = make_queue(20)
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 3

    outcome = narrate_review_queue(
        queue, cfg, scope=NarrationScope(max_explanations=20),
        backend=MockBackend(), should_cancel=should_cancel)

    assert outcome.cancelled is True
    assert outcome.explained < 20


def test_narration_reports_progress(cfg):
    seen = []
    narrate_review_queue(
        make_queue(5), cfg, scope=NarrationScope(max_explanations=3),
        backend=MockBackend(),
        progress_callback=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


# ---------------------------------------------------------------------------
# 6. The core invariant: narration never alters a finding
# ---------------------------------------------------------------------------

FROZEN_FIELDS = ("finding_type", "rule_id", "soc_id", "severity",
                  "evidence", "queue_rank", "rationale")


def test_narration_never_alters_any_deterministic_field(cfg):
    queue = make_queue(10)
    before = [{k: str(r.get(k)) for k in FROZEN_FIELDS} for r in queue]

    narrate_review_queue(queue, cfg, scope=NarrationScope(max_explanations=10),
                          backend=MockBackend())

    after = [{k: str(r.get(k)) for k in FROZEN_FIELDS} for r in queue]
    assert before == after, "narration modified a deterministic field"


def test_narration_only_adds_narration_prefixed_keys(cfg):
    queue = make_queue(3)
    before = set(queue[0])
    narrate_review_queue(queue, cfg, scope=NarrationScope(max_explanations=3),
                          backend=MockBackend())
    added = set(queue[0]) - before
    assert added, "narration added nothing at all"
    assert all(k.startswith("narrat") for k in added), added


def test_queue_order_is_untouched_by_narration(cfg):
    queue = make_queue(10)
    ranks_before = [r["queue_rank"] for r in queue]
    narrate_review_queue(queue, cfg, scope=NarrationScope(max_explanations=5),
                          backend=MockBackend())
    assert [r["queue_rank"] for r in queue] == ranks_before
