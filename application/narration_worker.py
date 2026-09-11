from PySide6.QtCore import QObject, Signal, Slot

from application.services.ai_config import AIConfig
from application.services.narration_service import NarrationService


class NarrationWorker(QObject):
    """
    Generates AI explanations in the background.

    The worker only explains existing SAT-SA findings. It never creates,
    removes, or modifies findings — it delegates to NarrationService,
    which writes nothing but narration fields.

    Cancellable: a 7B model on CPU takes minutes per explanation, so a
    supervisor must be able to stop a run without killing the window.
    """

    progress = Signal(int, int)          # done, total
    status_ready = Signal(object)        # BackendStatus
    finished = Signal(object)            # NarrationOutcome
    failed = Signal(str)

    def __init__(self, review_queue, ai_config: AIConfig, results=None):
        super().__init__()
        self.review_queue = review_queue
        self.ai_config = ai_config
        self.results = results
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        try:
            service = NarrationService(config=self.ai_config)

            status = service.status()
            self.status_ready.emit(status)

            outcome = service.explain_review_queue(
                self.review_queue,
                progress_callback=lambda done, total: self.progress.emit(done, total),
                should_cancel=lambda: self._cancelled,
                results=self.results,
            )

            self.finished.emit(outcome)

        except Exception as exc:
            # The assessment is already complete and saved by this point;
            # a narration failure must never invalidate it.
            self.failed.emit(str(exc))
