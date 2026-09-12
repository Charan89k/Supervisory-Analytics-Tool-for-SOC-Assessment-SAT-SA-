"""
Optional local-AI setup: what state it is in, and how to change it.

SAT-SA assesses submissions without any of this. The deterministic
engine is authoritative and never consults a model; the local AI only
restates findings the rule engine has already decided. Every function
here is about an OPTIONAL convenience, and nothing in this module may
prevent the application from starting or an assessment from running.

DETECTION IS NOT REIMPLEMENTED HERE. `ollama_runtime.discover()` already
answers whether the runtime is installed, whether its service responds
and which models it has, and it never raises. This module classifies
that answer into the states a setup flow has to act on, and provides the
actions that move between them.

NOTHING HAPPENS WITHOUT CONSENT. No function here runs on import, on
startup, or as a side effect of inspection. `inspect()` is read-only.
The three actions below run only when a caller invokes them, which in
the application means a person clicked a button having been told what it
would do.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from analytics.narration import ollama_runtime
from analytics.narration.ollama_runtime import RuntimeStatus

#: The model the setup flow installs. Small deliberately: 3B answers in
#: tens of seconds on a CPU-only laptop where 7B takes minutes, and a
#: setup flow that leaves someone with an unusably slow model has not
#: helped them.
SETUP_MODEL = "qwen2.5:3b"
SETUP_MODEL_LABEL = "Qwen 2.5 3B"

#: States a setup flow must distinguish, because each needs a different
#: action from the user.
READY = "ready"                    # runtime up, model present
MODEL_MISSING = "model_missing"    # runtime up, model absent
SERVICE_STOPPED = "service_stopped"  # installed, service not responding
NOT_INSTALLED = "not_installed"    # no runtime at all

#: How long to wait for a service we just started to answer.
SERVICE_START_TIMEOUT = 30

#: Generous: `ollama pull` fetches roughly 2 GB for the 3B model.
PULL_TIMEOUT = 3600
INSTALL_TIMEOUT = 3600


@dataclass
class LocalAIState:
    """The current state of the optional local AI, and what it needs."""

    state: str
    runtime: RuntimeStatus
    model: str = SETUP_MODEL

    @property
    def ready(self) -> bool:
        return self.state == READY

    @property
    def model_name(self) -> str:
        """
        The display name for THIS state's model.

        Derived rather than constant: a state carrying a different model
        was reporting the default's label, so the dialog offered to
        "Install Qwen 2.5 3B" while the plan downloaded something else.
        """
        if self.model == SETUP_MODEL:
            return SETUP_MODEL_LABEL
        return self.model

    @property
    def runtime_label(self) -> str:
        if self.runtime.service_running:
            return "RUNNING"
        return "INSTALLED" if self.runtime.installed else "NOT INSTALLED"

    @property
    def model_label(self) -> str:
        if not self.runtime.service_running:
            return "UNKNOWN"
        return "INSTALLED" if self.runtime.has_model(self.model) else "NOT INSTALLED"

    def headline(self) -> str:
        return {
            READY: "LOCAL AI READY",
            MODEL_MISSING: "LOCAL AI NOT READY",
            SERVICE_STOPPED: "OLLAMA SERVICE NOT RUNNING",
            NOT_INSTALLED: "LOCAL AI SETUP",
        }[self.state]

    def summary(self) -> str:
        """What is true, phrased for someone who did not ask for AI."""
        if self.state == READY:
            return (f"Ollama is running and {self.model_name} is installed. "
                    f"Explanations run entirely on this machine.")
        if self.state == MODEL_MISSING:
            present = ", ".join(self.runtime.models) or "none"
            return (f"Ollama is installed and running, but "
                    f"{self.model_name} is not among its models "
                    f"(present: {present}).")
        if self.state == SERVICE_STOPPED:
            return (f"Ollama is installed but its local service is not "
                    f"responding at {self.runtime.endpoint}.")
        return ("SAT-SA can optionally use a locally running Qwen model to "
                "explain assessment findings. The assessment engine works "
                "without it.")

    def action_label(self) -> str:
        return {
            READY: "",
            MODEL_MISSING: f"Install {self.model_name}",
            SERVICE_STOPPED: "Start Ollama",
            NOT_INSTALLED: "Install Local AI",
        }[self.state]

    def needs_download(self) -> bool:
        """
        Whether acting on this state reaches the internet.

        Starting a stopped service does not. Installing a runtime or
        pulling a model does, and the user has to be told before they
        agree to it.
        """
        return self.state in (NOT_INSTALLED, MODEL_MISSING)


def inspect(model: str = SETUP_MODEL, timeout: int = 3,
             endpoint: Optional[str] = None) -> LocalAIState:
    """
    Read-only. Classifies what `ollama_runtime.discover()` found.

    Never raises, never starts or installs anything, and is safe to call
    on the UI thread — it is one filesystem lookup and one loopback GET
    against a short timeout.
    """
    # discover() binds its default endpoint at definition time, so it is
    # passed explicitly here rather than relying on the module constant.
    runtime = ollama_runtime.discover(
        endpoint=endpoint or ollama_runtime.DEFAULT_ENDPOINT, timeout=timeout)

    if not runtime.installed:
        state = NOT_INSTALLED
    elif not runtime.service_running:
        state = SERVICE_STOPPED
    elif runtime.has_model(model):
        state = READY
    else:
        state = MODEL_MISSING

    return LocalAIState(state=state, runtime=runtime, model=model)


# ----------------------------------------------------------------------
# Actions. Each returns (ok, message) and never raises.
# ----------------------------------------------------------------------

def _run(command: List[str], timeout: int,
          on_output: Optional[Callable[[str], None]] = None) -> tuple:
    """
    Run a command, streaming its output to `on_output`.

    Returns (returncode, combined_output). A missing executable, a
    timeout or an OS error is reported as a non-zero code with the
    reason, because a setup step failing is an ordinary outcome here and
    must never reach the user as a traceback.
    """
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, errors="replace",
        )
    except FileNotFoundError:
        return 127, f"{command[0]} was not found on this machine."
    except OSError as exc:
        return 1, f"Could not run {command[0]}: {exc}"

    lines: List[str] = []
    deadline = time.time() + timeout
    try:
        for line in process.stdout:                     # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                lines.append(line)
                if on_output:
                    on_output(line)
            if time.time() > deadline:
                process.kill()
                return 1, "\n".join(lines + ["Timed out."])
        process.wait(timeout=max(1, int(deadline - time.time())))
    except subprocess.TimeoutExpired:
        process.kill()
        return 1, "\n".join(lines + ["Timed out."])
    except Exception as exc:                            # pragma: no cover
        process.kill()
        return 1, "\n".join(lines + [str(exc)])

    return process.returncode or 0, "\n".join(lines)


def start_service(state: Optional[LocalAIState] = None,
                   on_output: Optional[Callable[[str], None]] = None) -> tuple:
    """
    Start the local Ollama service. No download, no install.

    Detached: `ollama serve` runs until stopped, so waiting on it would
    hang forever. Success is judged by the service answering its own
    loopback API, not by the launch returning.
    """
    state = state or inspect()
    executable = state.runtime.executable or "ollama"

    try:
        subprocess.Popen(
            [executable, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False, ("Ollama was not found on this machine, so its "
                       "service cannot be started.")
    except OSError as exc:
        return False, f"Could not start the Ollama service: {exc}"

    for _ in range(SERVICE_START_TIMEOUT):
        time.sleep(1)
        running, _models = ollama_runtime.query_service(state.runtime.endpoint)
        if running:
            return True, "The Ollama service is responding."
        if on_output:
            on_output("waiting for the service to respond…")

    return False, (f"The Ollama service did not respond at "
                   f"{state.runtime.endpoint} within "
                   f"{SERVICE_START_TIMEOUT} seconds.")


def pull_model(model: str = SETUP_MODEL,
                state: Optional[LocalAIState] = None,
                on_output: Optional[Callable[[str], None]] = None) -> tuple:
    """
    Download a model into the local runtime. REACHES THE INTERNET.

    Only ever called after explicit consent. Uses Ollama's own client
    against Ollama's own registry — no third-party URL, no script
    fetched and executed.
    """
    state = state or inspect(model)
    executable = state.runtime.executable or "ollama"

    code, output = _run([executable, "pull", model], PULL_TIMEOUT, on_output)
    if code != 0:
        return False, output or f"`ollama pull {model}` failed."

    # Trust the runtime, not the exit code: ask what it actually has.
    refreshed = inspect(model)
    if not refreshed.runtime.has_model(model):
        return False, (f"`ollama pull {model}` reported success but the "
                       f"runtime does not list the model.")
    return True, f"{model} is installed."


def install_runtime(on_output: Optional[Callable[[str], None]] = None,
                     model: str = SETUP_MODEL,
                     script: Optional[Path] = None) -> tuple:
    """
    Install the Ollama runtime. REACHES THE INTERNET.

    Windows only, and deliberately so. It delegates to the reviewed
    `SAT-SA-Setup-AI.ps1` that ships with the package, which verifies
    the installer's published SHA-256 before running it, pins the
    runtime to loopback, and touches no firewall rule or security
    setting.

    On Linux and macOS this returns instructions instead of running
    anything. The usual one-line install for those platforms pipes a
    downloaded script straight into a shell, which is exactly the thing
    a supervisory tool must not do on someone's behalf. Installing
    Ollama is one command the user can run themselves, having seen it.
    """
    system = platform.system()

    if system != "Windows":
        return False, (
            "Automatic installation is supported on Windows only.\n\n"
            f"On {system}, install Ollama yourself from https://ollama.com "
            "using your distribution's package manager or the official\n"
            "instructions, then start it and use "
            f"'{LocalAIState(NOT_INSTALLED, RuntimeStatus()).action_label()}' "
            "again — or simply run:\n\n"
            f"    ollama pull {model}\n\n"
            "SAT-SA will not download and execute an installation script "
            "on your behalf.")

    script = script or _setup_script()
    if script is None or not script.is_file():
        return False, ("The setup script SAT-SA-Setup-AI.ps1 was not found "
                       "beside the application, so there is nothing to run. "
                       "Install Ollama from https://ollama.com, then use "
                       "this dialog again.")

    # -ExecutionPolicy Bypass is scoped to this one process and expires
    # with it. The machine policy is not touched.
    command = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-Model", model, "-AllowDownload",
    ]
    code, output = _run(command, INSTALL_TIMEOUT, on_output)
    if code != 0:
        return False, output or f"The setup script exited with code {code}."

    refreshed = inspect(model)
    if not refreshed.ready:
        return False, (output + "\n\n" if output else "") + refreshed.summary()
    return True, f"Ollama is installed and {model} is available."


def _setup_script() -> Optional[Path]:
    """
    Locate SAT-SA-Setup-AI.ps1.

    Beside the executable in a packaged build, under packaging/ when
    running from source. Returns None rather than guessing.
    """
    from application import paths

    candidates = [
        paths.resource_root() / "SAT-SA-Setup-AI.ps1",
        paths.resource_root().parent / "SAT-SA-Setup-AI.ps1",
        paths.resource_root() / "packaging" / "SAT-SA-Setup-AI.ps1",
    ]
    if not paths.is_frozen():
        candidates.append(
            Path(__file__).resolve().parents[2] / "packaging"
            / "SAT-SA-Setup-AI.ps1")

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# ----------------------------------------------------------------------
# The plan a caller executes
# ----------------------------------------------------------------------

@dataclass
class Step:
    """One named unit of work, so the UI can report progress honestly."""

    label: str
    run: Callable[[Optional[Callable[[str], None]]], tuple]


def plan_for(state: LocalAIState) -> List[Step]:
    """
    The steps that would move this state to READY.

    Built from the state rather than hardcoded, so a machine that only
    needs the service started does not sit through an install it does
    not need.
    """
    model = state.model

    if state.state == READY:
        return []

    steps: List[Step] = []

    if state.state == NOT_INSTALLED:
        steps.append(Step(
            f"Installing Ollama and {model}",
            lambda cb, m=model: install_runtime(on_output=cb, model=m)))
        return steps

    if state.state == SERVICE_STOPPED:
        steps.append(Step(
            "Starting the Ollama service",
            lambda cb, s=state: start_service(state=s, on_output=cb)))

    steps.append(Step(
        f"Downloading {model}",
        lambda cb, m=model: pull_model(model=m, on_output=cb)))

    return steps
