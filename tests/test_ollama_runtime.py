"""
Zero-configuration AI discovery.

Every case here is driven by stubs. The suite must pass identically on
a machine with no Ollama, a machine with Ollama stopped, and the
developer's machine where it happens to be running with an unrelated
model pulled — so nothing below may consult the real runtime.
"""

import platform

import pytest
import requests

from analytics.narration import ollama_runtime
from analytics.narration.base import (
    STATE_AVAILABLE,
    STATE_MODEL_MISSING,
    STATE_NOT_INSTALLED,
    STATE_NOT_RUNNING,
)
from analytics.narration.ollama_backend import OllamaBackend


# ----------------------------------------------------------------------
# Stubs
# ----------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def service(monkeypatch):
    """Control what the local service appears to be doing."""

    def configure(models=None, *, up=True, status=200):
        def fake_get(url, timeout=None):
            assert url.startswith("http://localhost") or \
                   url.startswith("http://127.0.0.1"), \
                   f"discovery must stay on loopback, got {url}"
            if not up:
                raise requests.ConnectionError("refused")
            return FakeResponse(
                {"models": [{"name": n} for n in (models or [])]}, status)

        monkeypatch.setattr(requests, "get", fake_get)

    return configure


@pytest.fixture
def executable(monkeypatch):
    """Control whether an Ollama executable appears to be installed."""

    def configure(path):
        monkeypatch.setattr(ollama_runtime.shutil, "which",
                            lambda name: path)
        # Block the platform-install fallback so a developer machine's
        # real /usr/local/bin/ollama cannot leak into a test.
        monkeypatch.setattr(ollama_runtime.os.path, "isfile",
                            lambda p: False)

    return configure


# ----------------------------------------------------------------------
# Executable discovery
# ----------------------------------------------------------------------

def test_path_lookup_wins(monkeypatch):
    monkeypatch.setattr(ollama_runtime.shutil, "which",
                        lambda name: "/usr/bin/ollama")
    assert ollama_runtime.find_executable() == "/usr/bin/ollama"


def test_falls_back_to_platform_install_location(monkeypatch):
    """Installed, but not yet on this shell's PATH — common on Windows."""
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ollama_runtime.os.path, "isfile",
        lambda p: p == "/opt/ollama/bin/ollama")
    assert ollama_runtime.find_executable() == "/opt/ollama/bin/ollama"


def test_returns_none_when_not_installed(monkeypatch):
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(ollama_runtime.os.path, "isfile", lambda p: False)
    assert ollama_runtime.find_executable() is None


def test_unexpanded_windows_variables_are_not_probed(monkeypatch):
    """
    %ProgramFiles(x86)% does not exist on a 32-bit host; expandvars
    leaves it verbatim. Treating that literal string as a path would be
    a silent nonsense lookup.
    """
    monkeypatch.setattr(ollama_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)

    probed = []

    def record(path):
        probed.append(path)
        return False

    monkeypatch.setattr(ollama_runtime.os.path, "isfile", record)
    ollama_runtime.find_executable()
    assert not any("%" in path for path in probed), probed


# ----------------------------------------------------------------------
# Service query
# ----------------------------------------------------------------------

def test_dead_service_is_an_answer_not_an_exception(service):
    service(up=False)
    assert ollama_runtime.query_service() == (False, [])


def test_http_error_is_an_answer_not_an_exception(service):
    service(["qwen2.5:7b"], status=500)
    assert ollama_runtime.query_service() == (False, [])


def test_running_service_reports_its_models(service):
    service(["qwen2.5:7b", "llama3:8b"])
    running, models = ollama_runtime.query_service()
    assert running
    assert models == ["llama3:8b", "qwen2.5:7b"]


# ----------------------------------------------------------------------
# The four states
# ----------------------------------------------------------------------

def test_nothing_installed(executable, service):
    executable(None)
    service(up=False)
    status = ollama_runtime.discover()
    assert not status.installed
    assert not status.service_running
    assert "not detected" in status.detail


def test_installed_but_service_stopped(executable, service):
    executable("/usr/local/bin/ollama")
    service(up=False)
    status = ollama_runtime.discover()
    assert status.installed
    assert not status.service_running
    assert "/usr/local/bin/ollama" in status.detail


def test_service_running_without_executable_on_path(executable, service):
    """
    Ollama in a container or under a service manager puts nothing on
    this user's PATH. A responding service is proof enough; reporting
    "not installed" here would send the operator to an installer they
    do not need.
    """
    executable(None)
    service(["qwen2.5:7b"])
    status = ollama_runtime.discover()
    assert status.installed
    assert status.service_running
    assert status.executable == ""


def test_running_with_no_models(executable, service):
    executable("/usr/local/bin/ollama")
    service([])
    status = ollama_runtime.discover()
    assert status.service_running
    assert status.models == []
    assert "no models" in status.detail


def test_ready(executable, service):
    executable("/usr/local/bin/ollama")
    service(["qwen2.5:7b"])
    status = ollama_runtime.discover()
    assert status.installed and status.service_running
    assert status.has_model("qwen2.5:7b")
    assert status.detail == ""


# ----------------------------------------------------------------------
# Model matching
# ----------------------------------------------------------------------

def test_model_matching_is_exact_or_by_tag():
    status = ollama_runtime.RuntimeStatus(models=["qwen2.5:7b"])
    assert status.has_model("qwen2.5:7b")      # exact
    assert status.has_model("qwen2.5")         # any tag of it
    assert not status.has_model("qwen2.5:14b")  # a different model
    assert not status.has_model("qwen")        # not a tag boundary
    assert not status.has_model("")


# ----------------------------------------------------------------------
# What the backend reports, and what the operator should do about it
# ----------------------------------------------------------------------

@pytest.mark.parametrize("installed,up,models,expected", [
    (None, False, [], STATE_NOT_INSTALLED),
    ("/usr/local/bin/ollama", False, [], STATE_NOT_RUNNING),
    ("/usr/local/bin/ollama", True, [], STATE_MODEL_MISSING),
    ("/usr/local/bin/ollama", True, ["llama3:8b"], STATE_MODEL_MISSING),
    ("/usr/local/bin/ollama", True, ["qwen2.5:7b"], STATE_AVAILABLE),
])
def test_probe_distinguishes_every_state(executable, service, installed, up,
                                          models, expected):
    """
    Each state implies a different fix, so a single "unavailable" would
    leave the operator with a red light and no next step.
    """
    executable(installed)
    service(models, up=up)

    status = OllamaBackend({"model": "qwen2.5:7b"}).probe()
    assert status.resolved_state() == expected
    assert status.available == (expected == STATE_AVAILABLE)


def test_every_failure_state_names_a_remedy(executable, service):
    for installed, up, models in [(None, False, []),
                                   ("/usr/local/bin/ollama", False, []),
                                   ("/usr/local/bin/ollama", True, [])]:
        executable(installed)
        service(models, up=up)
        status = OllamaBackend({"model": "qwen2.5:7b"}).probe()
        assert status.remedy(), f"no remedy for {status.resolved_state()}"
        assert status.detail


def test_ready_state_needs_no_remedy(executable, service):
    executable("/usr/local/bin/ollama")
    service(["qwen2.5:7b"])
    assert OllamaBackend({"model": "qwen2.5:7b"}).probe().remedy() == ""


def test_probe_never_raises_when_requests_is_absent(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_requests(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("no requests")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_requests)
    status = OllamaBackend({"model": "qwen2.5:7b"}).probe()
    assert not status.available


# ----------------------------------------------------------------------
# Air-gap guarantees
# ----------------------------------------------------------------------

def test_endpoint_is_loopback_only():
    assert ollama_runtime.DEFAULT_ENDPOINT.startswith("http://localhost")


def test_discovery_neither_installs_nor_starts_anything():
    """
    Discovery is read-only by design: a supervisory tool must not
    install software or start services on an assessed machine. This
    asserts the module has no mechanism to.
    """
    source = open(ollama_runtime.__file__).read()
    for forbidden in ("subprocess", "Popen", "os.system", "urlretrieve",
                       "pip install", "ollama pull", "ollama serve"):
        assert forbidden not in source, f"discovery must not use {forbidden}"


def test_discovery_makes_no_call_off_this_machine(service, executable):
    """The loopback assertion in the stub is the real check here."""
    executable("/usr/local/bin/ollama")
    service(["qwen2.5:7b"])
    ollama_runtime.discover()
