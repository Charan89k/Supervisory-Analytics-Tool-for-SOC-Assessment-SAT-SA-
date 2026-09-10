"""
Settings page — the AI section.

Kept in its own module rather than added to the already-large ui.py, and
holding no analytics logic of its own: it edits an AIConfig, persists it
through SettingsService, and asks NarrationService for a status. The
inference backends are never touched from here.

Sections beyond AI (General, Data, Reports, System) arrive in Phase 14;
the layout leaves room for them rather than pretending they exist.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from analytics.narration.service import SEVERITY_SCOPES
from application.services.ai_config import MODE_LABELS, MODELS, AIConfig
from application.services.narration_service import NarrationService

#: Backend keys paired with what a supervisor should understand them to be.
BACKEND_CHOICES = [
    ("llamacpp", "Local model file (llama.cpp) — offline deployment"),
    ("ollama", "Ollama service — development only"),
    ("mock", "Sample explanations — no model, for demo and testing"),
]

SCOPE_LABELS = {
    "critical": "Critical only",
    "critical_high": "Critical + High",
    "critical_high_medium": "Critical + High + Medium",
    "all": "All eligible findings",
}


class SettingsPage(QWidget):
    """Edits the AI configuration and reports live backend status."""

    settings_saved = Signal(object)   # AIConfig

    def __init__(self, config: AIConfig):
        super().__init__()
        self.config = config
        self._build()
        self.load_from(config)
        self.refresh_status()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(35, 30, 35, 30)
        outer.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("page_title")
        outer.addWidget(title)

        description = QLabel(
            "SAT-SA runs entirely offline. No setting here sends data "
            "anywhere; the AI options below control a language model "
            "running on this machine."
        )
        description.setObjectName("page_description")
        description.setWordWrap(True)
        outer.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(16)

        layout.addWidget(self._ai_group())
        layout.addWidget(self._scope_group())
        layout.addWidget(self._advanced_group())
        layout.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll)

        buttons = QHBoxLayout()
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("settings_hint")
        buttons.addWidget(self.status_hint)
        buttons.addStretch()

        self.reset_button = QPushButton("Reset to Defaults")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.clicked.connect(self.reset_defaults)

        self.save_button = QPushButton("Save Settings")
        self.save_button.setObjectName("primary_button")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.clicked.connect(self.save)

        buttons.addWidget(self.reset_button)
        buttons.addWidget(self.save_button)
        outer.addLayout(buttons)

    def _ai_group(self) -> QGroupBox:
        group = QGroupBox("Local AI Explanations")
        form = QFormLayout(group)
        form.setSpacing(10)

        self.enabled_box = QCheckBox(
            "Generate plain-language explanations for prioritised findings"
        )
        self.enabled_box.toggled.connect(self._on_enabled_toggled)
        form.addRow(self.enabled_box)

        note = QLabel(
            "Explanations restate findings the rule engine has already "
            "decided. They never create, remove, or re-score a finding, "
            "and the deterministic evidence remains authoritative."
        )
        note.setObjectName("settings_note")
        note.setWordWrap(True)
        form.addRow(note)

        self.backend_box = QComboBox()
        for key, label in BACKEND_CHOICES:
            self.backend_box.addItem(label, key)
        self.backend_box.currentIndexChanged.connect(self._on_backend_changed)
        form.addRow("Backend", self.backend_box)

        self.model_box = QComboBox()
        for key in ("fast", "balanced", "deep"):
            self.model_box.addItem(MODE_LABELS[key], key)
        self.model_box.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Model quality", self.model_box)

        self.model_hint = QLabel("")
        self.model_hint.setObjectName("settings_note")
        self.model_hint.setWordWrap(True)
        form.addRow("", self.model_hint)

        path_row = QHBoxLayout()
        self.model_path_edit = QLineEdit()
        self.model_path_edit.setPlaceholderText(
            "Path to a local .gguf model file")
        self.browse_model_button = QPushButton("Browse…")
        self.browse_model_button.setCursor(Qt.PointingHandCursor)
        self.browse_model_button.clicked.connect(self.browse_model)
        path_row.addWidget(self.model_path_edit)
        path_row.addWidget(self.browse_model_button)
        self.model_path_row = QWidget()
        self.model_path_row.setLayout(path_row)
        path_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Model file", self.model_path_row)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Checking…")
        self.status_label.setObjectName("ai_status")
        self.status_label.setWordWrap(True)
        self.check_button = QPushButton("Check Status")
        self.check_button.setCursor(Qt.PointingHandCursor)
        self.check_button.clicked.connect(self.refresh_status)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.check_button)
        form.addRow("Status", status_row)

        return group

    def _scope_group(self) -> QGroupBox:
        group = QGroupBox("Explanation Scope")
        form = QFormLayout(group)
        form.setSpacing(10)

        explanation = QLabel(
            "An assessment produces far more findings than anyone will "
            "read, and a local model takes minutes per explanation. Scope "
            "follows the review queue's existing supervisory ranking."
        )
        explanation.setObjectName("settings_note")
        explanation.setWordWrap(True)
        form.addRow(explanation)

        self.max_explanations_spin = QSpinBox()
        self.max_explanations_spin.setRange(1, 500)
        self.max_explanations_spin.valueChanged.connect(self._update_cost_hint)
        form.addRow("Maximum explanations", self.max_explanations_spin)

        self.max_rank_spin = QSpinBox()
        self.max_rank_spin.setRange(0, 10_000)
        self.max_rank_spin.setSpecialValueText("No limit")
        form.addRow("Only queue ranks up to", self.max_rank_spin)

        self.scope_box = QComboBox()
        for key in ("critical", "critical_high", "critical_high_medium", "all"):
            if key in SEVERITY_SCOPES:
                self.scope_box.addItem(SCOPE_LABELS[key], key)
        form.addRow("Severity scope", self.scope_box)

        severity_note = QLabel(
            "Severity is a secondary control. The review queue is already "
            "almost entirely Critical and High by construction, so this "
            "setting narrows very little — the maximum above is what "
            "actually bounds the work."
        )
        severity_note.setObjectName("settings_note")
        severity_note.setWordWrap(True)
        form.addRow("", severity_note)

        self.cost_hint = QLabel("")
        self.cost_hint.setObjectName("settings_note")
        self.cost_hint.setWordWrap(True)
        form.addRow("", self.cost_hint)

        return group

    def _advanced_group(self) -> QGroupBox:
        group = QGroupBox("Advanced")
        group.setCheckable(True)
        group.setChecked(False)
        form = QFormLayout(group)
        form.setSpacing(10)

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 1.0)
        self.temperature_spin.setSingleStep(0.05)
        self.temperature_spin.setDecimals(2)
        form.addRow("Temperature", self.temperature_spin)

        temp_note = QLabel(
            "Low values keep explanations close to the recorded evidence. "
            "Raising this makes wording more varied and less predictable."
        )
        temp_note.setObjectName("settings_note")
        temp_note.setWordWrap(True)
        form.addRow("", temp_note)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 900)
        self.timeout_spin.setSuffix(" s")
        form.addRow("Generation timeout", self.timeout_spin)

        self.probe_timeout_spin = QSpinBox()
        self.probe_timeout_spin.setRange(1, 30)
        self.probe_timeout_spin.setSuffix(" s")
        form.addRow("Status check timeout", self.probe_timeout_spin)

        return group

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def load_from(self, config: AIConfig):
        self.config = config

        self.enabled_box.setChecked(config.enabled)

        index = self.backend_box.findData(config.backend)
        self.backend_box.setCurrentIndex(index if index >= 0 else 0)

        index = self.model_box.findData(config.mode)
        self.model_box.setCurrentIndex(index if index >= 0 else 1)

        self.model_path_edit.setText(config.model_path)
        self.max_explanations_spin.setValue(config.max_explanations)
        self.max_rank_spin.setValue(config.max_queue_rank or 0)

        index = self.scope_box.findData(config.severity_scope)
        self.scope_box.setCurrentIndex(index if index >= 0 else 1)

        self.temperature_spin.setValue(config.temperature)
        self.timeout_spin.setValue(config.timeout)
        self.probe_timeout_spin.setValue(config.availability_timeout)

        self._on_enabled_toggled(config.enabled)
        self._on_backend_changed()
        self._on_mode_changed()

    def to_config(self) -> AIConfig:
        mode = self.model_box.currentData() or "balanced"
        return AIConfig(
            enabled=self.enabled_box.isChecked(),
            backend=self.backend_box.currentData() or "mock",
            mode=mode,
            model=MODELS[mode],
            model_path=self.model_path_edit.text().strip(),
            severity_scope=self.scope_box.currentData() or "critical_high",
            max_explanations=self.max_explanations_spin.value(),
            max_queue_rank=(self.max_rank_spin.value() or None),
            temperature=self.temperature_spin.value(),
            timeout=self.timeout_spin.value(),
            availability_timeout=self.probe_timeout_spin.value(),
        )

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------

    def _on_enabled_toggled(self, enabled: bool):
        for widget in (self.backend_box, self.model_box, self.model_path_row,
                        self.max_explanations_spin, self.max_rank_spin,
                        self.scope_box):
            widget.setEnabled(enabled)
        self.refresh_status()

    def _on_backend_changed(self, *_):
        backend = self.backend_box.currentData()
        # A .gguf path is meaningful only to llama.cpp; Ollama resolves a
        # model by name and the sample explainer runs no model at all.
        self.model_path_row.setVisible(backend == "llamacpp")
        self.model_box.setEnabled(
            self.enabled_box.isChecked() and backend == "ollama")
        self._on_mode_changed()
        self.refresh_status()

    def _on_mode_changed(self, *_):
        backend = self.backend_box.currentData()
        mode = self.model_box.currentData()

        if backend == "mock":
            self.model_hint.setText(
                "Sample explanations are generated from each finding's own "
                "recorded evidence. They are clearly labelled as samples "
                "and are never presented as model output.")
        elif backend == "llamacpp":
            self.model_hint.setText(
                "The model is the .gguf file selected below. Updating it "
                "means replacing that file — no installer, no service.")
        elif mode == "deep":
            self.model_hint.setText(
                "Qwen 2.5 14B needs roughly 9 GB of RAM and is very slow "
                "on a CPU-only machine. Choose it only on hardware sized "
                "for it.")
        else:
            self.model_hint.setText(
                f"Ollama will be asked for '{MODELS.get(mode, '')}'. The "
                "model must already be pulled on this machine.")

        self._update_cost_hint()

    def _update_cost_hint(self, *_):
        count = self.max_explanations_spin.value()
        backend = self.backend_box.currentData()

        if not self.enabled_box.isChecked():
            self.cost_hint.setText(
                "AI is off. Every finding keeps its rule-generated "
                "rationale and the assessment is unaffected.")
        elif backend == "mock":
            self.cost_hint.setText(
                f"{count} sample explanation(s) will be generated instantly.")
        else:
            self.cost_hint.setText(
                f"{count} explanation(s). On a CPU-only machine expect "
                f"roughly {count}-{count * 4} minutes; the run can be "
                f"cancelled at any point.")

    def refresh_status(self, *_):
        status = NarrationService(config=self.to_config()).status()
        self.status_label.setText(status.label())
        self.status_label.setProperty("state", status.resolved_state())
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        return status

    def browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a local GGUF model file", "",
            "GGUF model (*.gguf);;All Files (*)")
        if path:
            self.model_path_edit.setText(path)
            self.refresh_status()

    def reset_defaults(self):
        self.load_from(AIConfig())
        self.status_hint.setText("Reset to defaults — not yet saved.")

    def save(self):
        self.config = self.to_config()
        self.settings_saved.emit(self.config)
        self.refresh_status()
        return self.config
