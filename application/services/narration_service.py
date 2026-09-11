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
from analytics.narration.prompt import validate_explanation
from analytics.narration.service import (
    NarrationOutcome,
    NarrationScope,
    build_context,
    case_siblings,
    finding_view,
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
        results: Optional[dict] = None,
    ) -> NarrationOutcome:
        """
        `results` is the finished assessment, passed through so each
        explanation can see its entity's context. Read-only, and
        optional: without it explanations still work from the finding
        and its evidence alone.
        """
        return narrate_review_queue(
            review_queue,
            self.config.to_narration_config(),
            scope=self.scope(),
            backend=self.backend,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            results=results,
        )

    def explain_finding(self, finding: dict,
                         results: Optional[dict] = None,
                         review_queue: Optional[List[dict]] = None) -> dict:
        """
        Explain ONE finding — the one the examiner is looking at.

        Returns a structured outcome rather than a bare string, because
        the caller has to tell the difference between "the model said
        this" and "the model could not be reached", and show the right
        thing either way. The finding itself is never modified here; the
        caller decides what to display.
        """
        outcome = {
            "success": False,
            "explanation": "",
            "backend": self.config.backend,
            "model": self.config.model,
            "finding_id": finding.get("finding_id") or finding.get("alert_id")
                          or finding.get("case_id"),
            "error": None,
        }

        if not self.config.enabled:
            outcome["error"] = ("Local AI explanations are switched off. "
                                "Enable them in Settings.")
            return outcome

        status = self.status()
        if not status.available:
            outcome["error"] = f"{status.headline()} — {status.detail}"
            return outcome

        context = build_context(results).get(str(finding.get("soc_id")))
        view = finding_view(finding, context)
        siblings = case_siblings(finding, review_queue)
        if siblings:
            view["case_sibling_findings"] = siblings

        try:
            result = self.backend.explain(view)
        except Exception as exc:                       # never reach the UI raw
            outcome["error"] = f"The local model could not be reached: {exc}"
            return outcome

        if result is None:
            outcome["error"] = ("The local model returned no explanation. "
                                "The finding and its evidence are unchanged.")
            return outcome

        usable, reason = validate_explanation(result.text, view)
        if not usable:
            outcome["error"] = f"The response was not usable — {reason}."
            return outcome

        outcome.update(success=True, explanation=result.text,
                        backend=result.backend, model=result.model,
                        is_mock=result.is_mock,
                        provenance=result.provenance())
        return outcome
