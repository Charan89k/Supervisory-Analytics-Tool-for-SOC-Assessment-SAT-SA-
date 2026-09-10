"""
Desktop bridge to the local explanation layer.

Owns no inference logic of its own — it resolves an AIConfig into a
backend, reports availability, and delegates scope selection and
narration to analytics.narration.service, which is the same code path
the CLI uses. Two implementations of "which findings get explained"
would eventually disagree.
"""

from typing import Callable, List, Optional

from analytics.narration import create_backend
from analytics.narration.base import (
    STATE_DISABLED,
    STATE_UNAVAILABLE,
    BackendStatus,
    LocalLLMBackend,
)
from analytics.narration.service import (
    NarrationOutcome,
    NarrationScope,
    narrate_review_queue,
)

from application.services.ai_config import AIConfig


class NarrationService:
    """
    Explains findings that already exist.

    It cannot create, remove, re-score, or re-rank a finding — it has
    no code path that writes anything but the narration fields.
    """

    def __init__(
        self,
        config: Optional[AIConfig] = None,
        backend: Optional[LocalLLMBackend] = None,
    ):
        self.config = config or AIConfig()
        self._backend = backend

    @property
    def backend(self) -> LocalLLMBackend:
        if self._backend is None:
            self._backend = create_backend(self.config.to_narration_config())
        return self._backend

    def status(self) -> BackendStatus:
        """
        Fast availability check for the UI's AI status indicator.
        Never raises and never blocks past the probe timeout, so a
        missing model shows as "AI unavailable" rather than a freeze.
        """
        if not self.config.enabled:
            # Returns before touching the backend: a disabled AI must
            # cost nothing — no port opened, no file stat, no model load.
            return BackendStatus(
                available=False,
                backend=self.config.backend,
                model=self.config.model,
                detail="AI explanations are turned off",
                state=STATE_DISABLED,
            )
        try:
            return self.backend.probe()
        except Exception as exc:  # a backend must not break the window
            return BackendStatus(
                available=False,
                backend=self.config.backend,
                model=self.config.model,
                detail=f"backend error: {exc}",
                state=STATE_UNAVAILABLE,
            )

    def scope(self) -> NarrationScope:
        return NarrationScope(
            enabled=self.config.enabled,
            max_explanations=self.config.max_explanations,
            severity_scope=self.config.severity_scope,
            max_queue_rank=self.config.max_queue_rank,
        )

    def explain_review_queue(
        self,
        review_queue: List[dict],
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> NarrationOutcome:
        return narrate_review_queue(
            review_queue,
            self.config.to_narration_config(),
            scope=self.scope(),
            backend=self.backend,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    def explain_finding(self, finding: dict) -> Optional[str]:
        """Single-finding narration, e.g. a 'Regenerate explanation' action."""
        if not self.config.enabled:
            return None
        result = self.backend.explain(finding)
        return result.text if result is not None else None
