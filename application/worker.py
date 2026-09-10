from PySide6.QtCore import QObject, Signal, Slot

from analytics.ingestion import IngestionError
from analytics.validator import DatasetValidationError

from application.services.assessment_service import (
    AssessmentService,
)


class ValidationWorker(QObject):
    """
    Runs the "AUTOMATIC DATA VALIDATION" step off the UI thread.

    Loading a multi-entity submission is I/O bound and can take several
    seconds on a large dataset; doing it inline would freeze the window
    at exactly the moment the supervisor expects feedback.
    """

    finished = Signal(object)   # ValidationReport
    failed = Signal(str)        # unreadable / unsupported input

    def __init__(self, dataset_path):
        super().__init__()
        self.dataset_path = dataset_path

    @Slot()
    def run(self):
        try:
            report = AssessmentService().validate(self.dataset_path)
            self.finished.emit(report)
        except IngestionError as exc:
            # Carries a message written for a supervisor, plus a remedy.
            self.failed.emit(exc.message())
        except Exception as exc:
            self.failed.emit(str(exc))


class AssessmentWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    validation_failed = Signal(object)  # ValidationReport

    def __init__(
        self,
        dataset_path,
        ai_config=None,
    ):
        super().__init__()

        self.dataset_path = dataset_path
        self.ai_config = ai_config

    @Slot()
    def run(self):
        try:
            service = AssessmentService()

            result = service.run_assessment(
                self.dataset_path,
                ai_config=self.ai_config,
                progress_callback=self.progress.emit,
            )

            self.finished.emit(result)

        except DatasetValidationError as exc:
            # Not a crash: the dataset was read successfully and found
            # unfit. Routed separately so the UI can render the full
            # validation report instead of a one-line error string.
            self.validation_failed.emit(exc.report)

        except IngestionError as exc:
            self.failed.emit(exc.message())

        except Exception as exc:
            self.failed.emit(str(exc))
