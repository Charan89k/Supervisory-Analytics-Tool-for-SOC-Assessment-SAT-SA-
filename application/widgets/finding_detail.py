"""
The finding detail panel, shared by the Findings explorer and the
Review Queue.

Shared deliberately rather than duplicated. The panel enforces the rule
the whole product turns on — that an AI explanation is structurally
incapable of entering the authoritative record — and two copies of that
rule would eventually drift, with one losing the property quietly.

The panel presents, in order:

    FINDING      identifiers, verbatim
    RULE         what the rule tests, with thresholds from the live config
    RATIONALE    verbatim from the rule engine
    EVIDENCE     verbatim from the rule engine
    [WHY PRIORITISED]  optional, supplied by the review queue
    SOURCE RECORDS     read back from the submission on demand
    AI EXPLANATION     a separate widget, below and outside the above

Nothing here rephrases, summarises, or re-derives a finding.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from application.services import capability_service, rule_reference

RULE_SEPARATOR = "─" * 46


class FindingDetailPanel(QWidget):
    """Read-only presentation of one finding and its audit chain."""

    source_requested = Signal()

    def __init__(self, source_hint: str = ""):
        super().__init__()
        self.current_finding = None
        self._build(source_hint)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self, source_hint: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(8)

        self.title = QLabel("Select a finding")
        self.title.setObjectName("detail_title")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.provenance = QLabel("")
        self.provenance.setObjectName("caveat")
        self.provenance.setWordWrap(True)
        layout.addWidget(self.provenance)

        # FINDING / RULE / RATIONALE / EVIDENCE — all deterministic.
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setObjectName("deterministic_detail")
        self.detail.setMinimumHeight(280)
        layout.addWidget(self.detail)

        # SOURCE RECORDS
        source_header = QHBoxLayout()
        source_title = QLabel("SOURCE RECORDS")
        source_title.setObjectName("section_title")
        source_header.addWidget(source_title)
        source_header.addStretch()
        self.source_button = QPushButton("Load source records")
        self.source_button.setCursor(Qt.PointingHandCursor)
        self.source_button.clicked.connect(self.source_requested.emit)
        source_header.addWidget(self.source_button)
        layout.addLayout(source_header)

        self.source_hint = QLabel(source_hint or (
            "The submitted records this finding was derived from, read "
            "back from the dataset exactly as supplied."))
        self.source_hint.setObjectName("caveat")
        self.source_hint.setWordWrap(True)
        layout.addWidget(self.source_hint)

        self.source_detail = QTextEdit()
        self.source_detail.setReadOnly(True)
        self.source_detail.setObjectName("source_detail")
        self.source_detail.setMinimumHeight(200)
        layout.addWidget(self.source_detail)

        # AI EXPLANATION — its own widget. Narration text has nowhere to
        # go inside the panels above.
        self.ai_panel = QFrame()
        self.ai_panel.setObjectName("ai_panel")
        ai_layout = QVBoxLayout(self.ai_panel)

        self.ai_header = QLabel("")
        self.ai_header.setObjectName("ai_panel_header")
        self.ai_header.setWordWrap(True)
        ai_layout.addWidget(self.ai_header)

        self.ai_text = QTextEdit()
        self.ai_text.setReadOnly(True)
        self.ai_text.setObjectName("ai_panel_text")
        self.ai_text.setMaximumHeight(150)
        ai_layout.addWidget(self.ai_text)

        self.ai_footer = QLabel("")
        self.ai_footer.setObjectName("caveat")
        self.ai_footer.setWordWrap(True)
        ai_layout.addWidget(self.ai_footer)

        self.ai_panel.hide()
        layout.addWidget(self.ai_panel)

        layout.addStretch()
        scroll.setWidget(host)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    def clear(self, message: str = "Select a finding"):
        self.current_finding = None
        self.title.setText(message)
        self.provenance.clear()
        self.detail.clear()
        self.source_detail.clear()
        self.source_button.setEnabled(False)
        self.source_button.setText("Load source records")
        self.ai_panel.hide()
        self.ai_text.clear()

    def show_finding(self, finding: dict, config: dict,
                      narration: Optional[dict] = None,
                      extra_section: Optional[tuple] = None):
        """
        Render one finding.

        `extra_section` is an optional (heading, body) pair the caller
        may add between EVIDENCE and SOURCE RECORDS — the review queue
        uses it for the prioritisation arithmetic. It is deterministic
        content supplied by the caller, never generated here.
        """
        self.current_finding = finding

        severity = severity_label(finding)
        finding_type = str(finding.get("finding_type", "UNKNOWN"))
        rule_id = str(finding.get("rule_id", "UNKNOWN"))

        self.title.setText(f"{severity} — {finding_type}")
        self.provenance.setText(
            "Produced by a deterministic rule. Every value below is "
            "recorded output, not interpretation.")

        # A findings-list row carries _category; a review-queue record
        # does not. Fall back to the rule catalogue so both views show
        # the same thing rather than one showing "N/A".
        category = (finding.get("_category")
                    or rule_reference.category_for(rule_id))
        capability = capability_service.capability_for(finding_type, config)

        lines = ["FINDING", RULE_SEPARATOR]
        if category:
            lines.append(f"Category            {category}")
        if capability is not None:
            areas = capability_service.load_areas(config)
            area = areas.get(capability.primary)
            lines.append(
                f"Capability area     {area.label if area else capability.primary}")
        lines += [
            f"Finding type        {finding_type}",
            f"Severity            {severity}",
            f"Entity              {finding.get('soc_id', 'N/A')}",
            f"Alert               {finding.get('alert_id') or '—'}",
            f"Case                {finding.get('case_id') or '—'}",
            f"Analyst             {finding.get('assigned_analyst_id') or '—'}",
            "",
            f"RULE — {rule_id}",
            RULE_SEPARATOR,
        ]

        rule = rule_reference.describe(rule_id, config)
        if rule:
            lines.append(rule["checks"])
            if rule["thresholds"]:
                lines.append("")
                lines.append("Configured thresholds used by this rule:")
                for key, value in rule["thresholds"]:
                    lines.append(f"  {key} = {value}")
        else:
            lines.append("No rule description available for this rule id.")

        if capability is not None and capability.why:
            areas = capability_service.load_areas(config)
            area = areas.get(capability.primary)
            lines += [
                "",
                f"CAPABILITY AREA — {area.label if area else capability.primary}",
                RULE_SEPARATOR,
                capability.why,
            ]
            if capability.secondary:
                secondary = ", ".join(
                    (areas[key].label if key in areas else key)
                    for key in capability.secondary)
                lines.append("")
                lines.append(f"Also informs: {secondary} (context only, "
                              f"not scored against those areas).")

        lines += [
            "",
            "RATIONALE (deterministic)",
            RULE_SEPARATOR,
            str(finding.get("rationale", "No rationale recorded.")),
            "",
            "EVIDENCE (deterministic)",
            RULE_SEPARATOR,
            json.dumps(finding.get("evidence", {}), indent=2, default=str),
        ]

        if extra_section:
            heading, body = extra_section
            lines += ["", heading, RULE_SEPARATOR, body]

        self.detail.setPlainText("\n".join(lines))

        self.source_detail.clear()
        self.source_button.setEnabled(True)
        self.source_button.setText("Load source records")

        self.show_narration(narration)

    def show_narration(self, record: Optional[dict]):
        if not record:
            self.ai_panel.hide()
            self.ai_text.clear()
            return

        is_mock = bool(record.get("narration_is_mock"))
        self.ai_header.setText(
            "SAMPLE EXPLANATION — NO LANGUAGE MODEL" if is_mock
            else f"AI EXPLANATION — {record.get('narration_model', 'local model')}")
        self.ai_header.setProperty("state", "mock" if is_mock else "model")
        self.ai_header.style().unpolish(self.ai_header)
        self.ai_header.style().polish(self.ai_header)

        provenance = record.get("narration_provenance", "")
        self.ai_text.setPlainText(record.get("narrated_explanation", ""))
        self.ai_footer.setText(
            (provenance + "\n\n" if provenance else "")
            + "Supplementary only. The rule, rationale and evidence above "
            "are the authoritative record and were produced without it.")
        self.ai_panel.show()

    # ------------------------------------------------------------------
    # Source records
    # ------------------------------------------------------------------

    def set_source_text(self, text: str):
        self.source_detail.setPlainText(text)
        self.source_button.setEnabled(True)
        self.source_button.setText("Reload source records")

    def set_source_loading(self):
        self.source_button.setEnabled(False)
        self.source_button.setText("Loading…")


def severity_label(finding: dict) -> str:
    value = finding.get("severity")
    if value is None:
        return "UNRATED"
    text = str(value).strip().upper()
    return text if text in ("CRITICAL", "HIGH", "MEDIUM", "LOW") else "UNRATED"
