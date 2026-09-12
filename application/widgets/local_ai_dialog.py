"""
The optional local-AI setup dialog.

Everything here is optional and dismissible. SAT-SA assesses submissions
without a model, so this dialog may always be closed, and closing it is
a normal outcome rather than a cancelled installation.

TWO RULES SHAPE THE WHOLE THING.

Nothing is installed or downloaded without a person clicking a button
having read what it will do. The dialog opens on a description of the
current state and what the action would involve; the action runs only
on that click.

Nothing blocks the UI thread. Detection is a filesystem lookup and one
loopback GET, so it is cheap enough to run inline; installing a runtime
or pulling two gigabytes is not, and runs in a worker with the buttons
disabled while it does.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from application.services import local_ai_setup as setup


class SetupWorker(QObject):
    """Runs the setup plan off the UI thread."""

    step_started = Signal(str)
    output = Signal(str)
    step_finished = Signal(str, bool, str)     # label, ok, message
    finished = Signal(bool, str)               # overall ok, message

    def __init__(self, steps):
        super().__init__()
        self.steps = steps
        self._cancelled = False

    def cancel(self):
        """
        Ask to stop after the step in flight.

        A download or an installer is not killed mid-write: a
        half-installed runtime is worse than a slow one. The flag is
        checked between steps.
        """
        self._cancelled = True

    def run(self):
        for step in self.steps:
            if self._cancelled:
                self.finished.emit(False, "Setup was cancelled.")
                return

            self.step_started.emit(step.label)
            try:
                ok, message = step.run(self.output.emit)
            except Exception as exc:        # a step must never escape
                ok, message = False, f"{type(exc).__name__}: {exc}"

            self.step_finished.emit(step.label, ok, message)
            if not ok:
                self.finished.emit(False, message)
                return

        state = setup.inspect()
        if state.ready:
            self.finished.emit(True, state.summary())
        else:
            self.finished.emit(False, state.summary())


class LocalAISetupDialog(QDialog):
    """
    Shows what the local AI needs, and offers to provide it.

    `result_ready` reports whether the local AI ended up usable, so the
    caller can refresh its own status display. A closed dialog reports
    whatever the state actually is, not a failure.
    """

    def __init__(self, parent=None, state=None):
        super().__init__(parent)
        self.setWindowTitle("Local AI Setup")
        self.setMinimumWidth(560)
        self.setObjectName("local_ai_dialog")

        self.state = state or setup.inspect()
        self.worker = None
        self.thread = None
        self.busy = False
        self.completed_steps = []

        self._build()
        self._render_state()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(12)

        self.headline = QLabel("")
        self.headline.setObjectName("page_title")
        layout.addWidget(self.headline)

        self.summary = QLabel("")
        self.summary.setObjectName("page_description")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        # ---- what is present -----------------------------------------
        status_panel = QFrame()
        status_panel.setObjectName("panel")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setSpacing(4)

        self.runtime_row = QLabel("")
        self.runtime_row.setObjectName("status_item")
        self.model_row = QLabel("")
        self.model_row.setObjectName("status_item")
        status_layout.addWidget(self.runtime_row)
        status_layout.addWidget(self.model_row)
        layout.addWidget(status_panel)

        # ---- what the action would do --------------------------------
        # Shown BEFORE anything runs. Consent means nothing if the
        # consequence is only described afterwards.
        self.consent = QLabel("")
        self.consent.setObjectName("validation_caveat")
        self.consent.setWordWrap(True)
        layout.addWidget(self.consent)

        # ---- progress -------------------------------------------------
        self.progress = QProgressBar()
        self.progress.setObjectName("narration_progress")
        # Indeterminate: `ollama pull` reports its own percentage in
        # text, but not in a form worth parsing into a bar. A bar that
        # invents a percentage is worse than one that admits it is
        # working.
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("source_detail")
        self.log.setMinimumHeight(150)
        self.log.hide()
        layout.addWidget(self.log)

        # ---- buttons --------------------------------------------------
        buttons = QHBoxLayout()
        buttons.addStretch()

        self.action_button = QPushButton("")
        self.action_button.setObjectName("primary_button")
        self.action_button.setMinimumHeight(36)
        # #primary_button sets no horizontal padding, so the label was
        # clipping at its natural width.
        self.action_button.setMinimumWidth(190)
        self.action_button.setCursor(Qt.PointingHandCursor)
        self.action_button.clicked.connect(self.start)
        buttons.addWidget(self.action_button)

        self.dismiss_button = QPushButton("Continue Without AI")
        self.dismiss_button.setObjectName("browse_button")
        self.dismiss_button.setMinimumHeight(36)
        self.dismiss_button.setMinimumWidth(190)
        self.dismiss_button.setCursor(Qt.PointingHandCursor)
        self.dismiss_button.clicked.connect(self.reject)
        buttons.addWidget(self.dismiss_button)

        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # State rendering
    # ------------------------------------------------------------------

    def _render_state(self):
        state = self.state
        self.headline.setText(state.headline())
        self.summary.setText(state.summary())
        self.runtime_row.setText(f"Ollama            {state.runtime_label}")
        self.model_row.setText(
            f"{state.model_name}       {state.model_label}")

        if state.ready:
            self.consent.setText("")
            self.consent.hide()
            self.action_button.hide()
            self.dismiss_button.setText("Continue")
            return

        self.consent.show()
        if state.needs_download():
            self.consent.setText(
                "Installing Local AI downloads the required software and "
                "model from the internet. Nothing is downloaded until you "
                "select the button below.\n\n"
                "Your SOC assessment data stays on this machine: once "
                "installed, inference runs locally and SAT-SA contacts no "
                "external service.")
        else:
            self.consent.setText(
                "This starts the Ollama service already installed on this "
                "machine. Nothing is downloaded.")

        self.action_button.setText(state.action_label())
        self.action_button.show()
        self.dismiss_button.setText("Continue Without AI")

    # ------------------------------------------------------------------
    # Running the plan
    # ------------------------------------------------------------------

    def start(self):
        if self.busy:
            return

        steps = setup.plan_for(self.state)
        if not steps:
            self.refresh()
            return

        self.busy = True
        self.completed_steps = []
        self.action_button.setEnabled(False)
        self.dismiss_button.setEnabled(False)
        self.consent.hide()
        self.progress.show()
        self.log.show()
        self.log.clear()

        self.thread = QThread()
        self.worker = SetupWorker(steps)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.step_started.connect(self._on_step_started)
        self.worker.output.connect(self._on_output)
        self.worker.step_finished.connect(self._on_step_finished)
        self.worker.finished.connect(self._on_finished)

        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def _on_step_started(self, label):
        self.headline.setText("LOCAL AI SETUP")
        self.summary.setText(f"{label}…")
        self._append(f"\n{label}…")

    def _on_output(self, line):
        self._append(f"    {line}")

    def _on_step_finished(self, label, ok, message):
        self.completed_steps.append((label, ok))
        self._append(f"    {'done' if ok else 'FAILED'}")
        if not ok and message:
            self._append(f"    {message}")

    def _on_finished(self, ok, message):
        self.busy = False
        self.progress.hide()
        self.dismiss_button.setEnabled(True)

        self.state = setup.inspect()

        if ok and self.state.ready:
            self.headline.setText("LOCAL AI READY")
            self.summary.setText(
                f"Runtime: Ollama\n"
                f"Model: {self.state.model_name}\n"
                f"Inference: local, on this machine")
            self.runtime_row.setText(f"Ollama            {self.state.runtime_label}")
            self.model_row.setText(
                f"{self.state.model_name}       {self.state.model_label}")
            self.action_button.hide()
            self.dismiss_button.setText("Continue")
            return

        # Failure. SAT-SA is unaffected, and the dialog says so rather
        # than leaving someone wondering what they have just broken.
        self.headline.setText("LOCAL AI SETUP FAILED")
        self.summary.setText(
            f"{message}\n\n"
            "SAT-SA assessment functionality is unaffected. Every finding, "
            "score and report is produced without the local AI.")
        self.action_button.setText("Retry")
        self.action_button.setEnabled(True)
        self.action_button.show()
        self.dismiss_button.setText("Continue Without AI")

    def _append(self, text):
        self.log.append(text)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())

    def refresh(self):
        self.state = setup.inspect()
        self._render_state()

    # ------------------------------------------------------------------

    def local_ai_ready(self) -> bool:
        return self.state.ready

    def reject(self):
        """
        Closing is always allowed.

        A setup step in flight is asked to stop after it finishes; a
        download killed mid-write can leave a partial model behind.
        """
        if self.busy and self.worker is not None:
            self.worker.cancel()
            self._append("\nCancelling after the current step…")
            return
        super().reject()
