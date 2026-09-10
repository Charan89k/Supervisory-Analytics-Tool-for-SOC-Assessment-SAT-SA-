"""
Application lifecycle state.

One object answers "where is this session?", and the UI asks it rather
than inferring an answer from whichever attributes happen to be set.
Before this, page gating would have meant checks like
`if self.current_result and not self.assessment_busy` scattered across
every page, which drift apart the moment a new phase appears.

The lifecycle the product actually has:

    NO_DATASET -> VALIDATING -> READY -> ASSESSING -> LOADED
                       |
                       +------> INVALID  (blocking validation errors)

Narration is deliberately NOT a phase. It runs after LOADED is reached
and cannot move the session out of it: the assessment is complete and
authoritative before an explanation is ever requested, and a failed or
cancelled narration leaves a fully valid loaded assessment behind.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject, Signal


class Phase(Enum):
    NO_DATASET = "no_dataset"
    VALIDATING = "validating"
    READY = "ready"
    INVALID = "invalid"
    ASSESSING = "assessing"
    LOADED = "loaded"


#: What a supervisor should do next, per phase. Shown in empty states
#: and the status bar, so a blank page always explains itself.
GUIDANCE = {
    Phase.NO_DATASET: "Select a dataset to begin an assessment.",
    Phase.VALIDATING: "Validating the selected dataset…",
    Phase.READY: "Dataset validated. Run the assessment to produce findings.",
    Phase.INVALID: "The dataset has blocking errors and cannot be assessed.",
    Phase.ASSESSING: "Assessment in progress…",
    Phase.LOADED: "Assessment loaded.",
}

STATUS_LABELS = {
    Phase.NO_DATASET: "No dataset",
    Phase.VALIDATING: "Validating",
    Phase.READY: "Ready to assess",
    Phase.INVALID: "Validation failed",
    Phase.ASSESSING: "Assessing",
    Phase.LOADED: "Assessment loaded",
}


class SessionState(QObject):
    """Holds the current lifecycle phase and notifies on every change."""

    changed = Signal(object)   # Phase

    def __init__(self):
        super().__init__()
        self._phase = Phase.NO_DATASET
        self.dataset_path: Optional[str] = None
        self.dataset_label: str = ""
        self.last_assessment_at: Optional[str] = None

    # -- phase -------------------------------------------------------

    @property
    def phase(self) -> Phase:
        return self._phase

    def transition(self, phase: Phase) -> None:
        if phase is self._phase:
            return
        self._phase = phase
        self.changed.emit(phase)

    # -- derived capabilities ---------------------------------------
    #
    # Every gate in the UI reads one of these rather than testing
    # attributes, so adding a phase later means editing this file only.

    @property
    def has_results(self) -> bool:
        """Results pages have something to show."""
        return self._phase is Phase.LOADED

    @property
    def can_run_assessment(self) -> bool:
        return self._phase in (Phase.READY, Phase.LOADED)

    @property
    def is_busy(self) -> bool:
        return self._phase in (Phase.VALIDATING, Phase.ASSESSING)

    @property
    def guidance(self) -> str:
        return GUIDANCE[self._phase]

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self._phase]

    # -- dataset -----------------------------------------------------

    def set_dataset(self, path: Optional[str], label: str = "") -> None:
        self.dataset_path = path
        self.dataset_label = label
