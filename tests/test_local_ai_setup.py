"""
Optional local-AI setup: state classification, plans, and safety.

The whole feature is optional. SAT-SA assesses submissions without a
model, so the properties that matter most here are negative ones:
nothing installs without being asked, inspection changes nothing, and a
failure at any point leaves the application usable.

Every test below is driven by stubs. None contacts Ollama, downloads
anything, or runs a subprocess.
"""

import platform
import subprocess

import pytest

from analytics.narration.ollama_runtime import RuntimeStatus
from application.services import local_ai_setup as setup


def runtime(installed=False, running=False, models=(), executable=""):
    return RuntimeStatus(
        installed=installed, service_running=running,
        models=list(models), executable=executable,
        endpoint="http://localhost:11434")


@pytest.fixture
def discovered(monkeypatch):
    """Control what discovery appears to find."""
    def configure(**kwargs):
        monkeypatch.setattr(setup.ollama_runtime, "discover",
                            lambda *a, **k: runtime(**kwargs))
    return configure


# ----------------------------------------------------------------------
# The four states
# ----------------------------------------------------------------------

def test_nothing_installed(discovered):
    discovered()
    state = setup.inspect()
    assert state.state == setup.NOT_INSTALLED
    assert state.headline() == "LOCAL AI SETUP"
    assert state.action_label() == "Install Local AI"
    assert state.runtime_label == "NOT INSTALLED"
    assert not state.ready


def test_installed_but_service_stopped(discovered):
    discovered(installed=True, running=False, executable="/usr/bin/ollama")
    state = setup.inspect()
    assert state.state == setup.SERVICE_STOPPED
    assert state.headline() == "OLLAMA SERVICE NOT RUNNING"
    assert state.action_label() == "Start Ollama"
    assert state.runtime_label == "INSTALLED"


def test_running_but_model_missing(discovered):
    discovered(installed=True, running=True, models=["llama3:8b"])
    state = setup.inspect()
    assert state.state == setup.MODEL_MISSING
    assert state.headline() == "LOCAL AI NOT READY"
    assert setup.SETUP_MODEL_LABEL in state.action_label()
    assert state.model_label == "NOT INSTALLED"
    assert "llama3:8b" in state.summary()


def test_ready(discovered):
    discovered(installed=True, running=True, models=["qwen2.5:3b"])
    state = setup.inspect()
    assert state.state == setup.READY
    assert state.ready
    assert state.headline() == "LOCAL AI READY"
    assert state.action_label() == ""
    assert state.model_label == "INSTALLED"


def test_model_matching_is_by_exact_tag(discovered):
    """
    Detection is delegated to RuntimeStatus.has_model. Ollama treats
    `qwen2.5:3b` and `qwen2.5:3b-instruct` as different models, so a
    near-miss must NOT read as ready — that would report AI READY and
    then fail on the first explanation.
    """
    discovered(installed=True, running=True, models=["qwen2.5:3b"])
    assert setup.inspect(model="qwen2.5:3b").state == setup.READY

    discovered(installed=True, running=True, models=["qwen2.5:3b-instruct"])
    assert setup.inspect(model="qwen2.5:3b").state == setup.MODEL_MISSING

    # An untagged request does match any tag of that model.
    discovered(installed=True, running=True, models=["qwen2.5:3b"])
    assert setup.inspect(model="qwen2.5").state == setup.READY


def test_the_default_model_is_the_small_one():
    """
    3B, not 7B. A setup flow that leaves someone with a model too slow
    to use on their laptop has not helped them.
    """
    assert setup.SETUP_MODEL == "qwen2.5:3b"


# ----------------------------------------------------------------------
# Consent: which actions reach the internet
# ----------------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    (setup.NOT_INSTALLED, True),
    (setup.MODEL_MISSING, True),
    (setup.SERVICE_STOPPED, False),
    (setup.READY, False),
])
def test_download_is_declared_before_it_happens(state, expected):
    """
    The dialog shows a different warning for actions that reach the
    internet. Getting this wrong would either alarm someone starting a
    local service, or download two gigabytes without saying so.
    """
    assert setup.LocalAIState(state, runtime()).needs_download() is expected


# ----------------------------------------------------------------------
# Plans
# ----------------------------------------------------------------------

def test_a_ready_machine_has_nothing_to_do():
    assert setup.plan_for(
        setup.LocalAIState(setup.READY, runtime(True, True, ["qwen2.5:3b"]))) == []


def test_a_stopped_service_is_started_then_the_model_fetched():
    steps = setup.plan_for(
        setup.LocalAIState(setup.SERVICE_STOPPED, runtime(installed=True)))
    labels = [s.label for s in steps]
    assert "Starting the Ollama service" in labels[0]
    assert setup.SETUP_MODEL in labels[1]


def test_a_missing_model_does_not_restart_a_running_service():
    steps = setup.plan_for(
        setup.LocalAIState(setup.MODEL_MISSING, runtime(True, True, ["x:1b"])))
    assert len(steps) == 1
    assert setup.SETUP_MODEL in steps[0].label


def test_a_missing_runtime_installs_once():
    steps = setup.plan_for(
        setup.LocalAIState(setup.NOT_INSTALLED, runtime()))
    assert len(steps) == 1
    assert "Installing Ollama" in steps[0].label


# ----------------------------------------------------------------------
# Nothing runs without being asked
# ----------------------------------------------------------------------

def test_inspect_never_starts_a_process(discovered, monkeypatch):
    """
    The most important property here. Inspection happens at startup;
    if it could install or start anything, SAT-SA would be modifying a
    supervisor's machine merely by being opened.
    """
    def forbidden(*a, **k):
        raise AssertionError("inspect() must not run a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    discovered(installed=True, running=True, models=["llama3:8b"])

    setup.inspect()


def test_building_a_plan_never_runs_it(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("plan_for() must not execute anything")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    setup.plan_for(setup.LocalAIState(setup.NOT_INSTALLED, runtime()))


# ----------------------------------------------------------------------
# Installation is Windows-only, and says so elsewhere
# ----------------------------------------------------------------------

def test_no_install_script_is_piped_into_a_shell(monkeypatch):
    """
    The usual Linux/macOS one-liner downloads a script and pipes it
    straight into a shell. A supervisory tool must not do that on
    someone's behalf, so those platforms get instructions instead.
    """
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    def forbidden(*a, **k):
        raise AssertionError("install must not execute anything on Linux")

    monkeypatch.setattr(subprocess, "Popen", forbidden)

    ok, message = setup.install_runtime()
    assert ok is False
    assert "Windows only" in message
    assert "ollama pull" in message
    assert "will not download and execute" in message


def test_a_missing_setup_script_is_reported_not_guessed(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup, "_setup_script", lambda: None)

    def forbidden(*a, **k):
        raise AssertionError("must not run anything without a script")

    monkeypatch.setattr(subprocess, "Popen", forbidden)

    ok, message = setup.install_runtime()
    assert ok is False
    assert "SAT-SA-Setup-AI.ps1" in message


def test_the_windows_command_is_scoped_and_declares_the_download(monkeypatch,
                                                                  tmp_path):
    """
    -ExecutionPolicy Bypass must be process-scoped, and -AllowDownload
    must be explicit rather than the script's default.
    """
    script = tmp_path / "SAT-SA-Setup-AI.ps1"
    script.write_text("# stub")
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    seen = {}

    def fake_run(command, timeout, on_output=None):
        seen["command"] = command
        return 0, "ok"

    monkeypatch.setattr(setup, "_run", fake_run)
    monkeypatch.setattr(setup, "inspect",
                        lambda *a, **k: setup.LocalAIState(
                            setup.READY, runtime(True, True, ["qwen2.5:3b"])))

    ok, _ = setup.install_runtime(script=script)
    command = seen["command"]

    assert ok is True
    assert command[0] == "powershell"
    assert "-NoProfile" in command
    assert command[command.index("-ExecutionPolicy") + 1] == "Bypass"
    assert "-AllowDownload" in command
    assert command[command.index("-Model") + 1] == setup.SETUP_MODEL
    assert "Invoke-Expression" not in " ".join(command)
    assert "iex" not in " ".join(command)


# ----------------------------------------------------------------------
# Failure never escapes
# ----------------------------------------------------------------------

def test_a_missing_executable_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    ok, message = setup.start_service(
        setup.LocalAIState(setup.SERVICE_STOPPED, runtime(installed=True)))
    assert ok is False
    assert "not found" in message


def test_a_service_that_never_answers_times_out_cleanly(monkeypatch):
    monkeypatch.setattr(setup, "SERVICE_START_TIMEOUT", 1)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(setup.ollama_runtime, "query_service",
                        lambda *a, **k: (False, []))
    monkeypatch.setattr(setup.time, "sleep", lambda s: None)

    ok, message = setup.start_service(
        setup.LocalAIState(setup.SERVICE_STOPPED, runtime(installed=True)))
    assert ok is False
    assert "did not respond" in message


def test_a_pull_that_lies_about_success_is_caught(monkeypatch):
    """
    Trust the runtime's model list, not the exit code. A pull that
    returns 0 without producing a model would otherwise leave the app
    reporting READY and then failing on every explanation.
    """
    monkeypatch.setattr(setup, "_run", lambda *a, **k: (0, "done"))
    monkeypatch.setattr(setup, "inspect",
                        lambda *a, **k: setup.LocalAIState(
                            setup.MODEL_MISSING, runtime(True, True, [])))

    ok, message = setup.pull_model()
    assert ok is False
    assert "does not list the model" in message


def test_a_failed_pull_reports_the_real_output(monkeypatch):
    monkeypatch.setattr(setup, "_run",
                        lambda *a, **k: (1, "Error: connection refused"))
    ok, message = setup.pull_model()
    assert ok is False
    assert "connection refused" in message


def test_run_reports_a_missing_command_rather_than_raising(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    code, message = setup._run(["definitely-not-a-command"], timeout=1)
    assert code != 0
    assert "not found" in message


# ----------------------------------------------------------------------
# The label must follow the model
# ----------------------------------------------------------------------

def test_the_label_follows_the_state_model_not_a_constant(discovered):
    """
    A state carrying a different model was reporting the default's
    label, so the dialog offered to "Install Qwen 2.5 3B" while the plan
    downloaded something else entirely.
    """
    discovered(installed=True, running=True, models=["qwen2.5:3b"])
    other = setup.inspect(model="qwen2.5:32b")

    assert other.state == setup.MODEL_MISSING
    assert other.action_label() == "Install qwen2.5:32b"
    assert "qwen2.5:32b" in other.summary()
    assert setup.SETUP_MODEL_LABEL not in other.action_label()

    # And the friendly label is still used for the model setup installs.
    default = setup.inspect(model=setup.SETUP_MODEL)
    assert default.model_name == setup.SETUP_MODEL_LABEL


def test_the_plan_downloads_the_model_the_label_names(discovered):
    discovered(installed=True, running=True, models=[])
    state = setup.inspect(model="qwen2.5:32b")
    steps = setup.plan_for(state)
    assert "qwen2.5:32b" in steps[0].label


def test_inspect_honours_an_explicit_endpoint(monkeypatch):
    """
    discover() binds its default endpoint at definition time, so
    reassigning the module constant does not reach it. inspect() must
    pass the endpoint through for a non-default deployment to work.
    """
    seen = {}

    def fake_discover(endpoint=None, timeout=3):
        seen["endpoint"] = endpoint
        return runtime(installed=True, running=True, models=["qwen2.5:3b"])

    monkeypatch.setattr(setup.ollama_runtime, "discover", fake_discover)
    setup.inspect(endpoint="http://127.0.0.1:9999")
    assert seen["endpoint"] == "http://127.0.0.1:9999"
