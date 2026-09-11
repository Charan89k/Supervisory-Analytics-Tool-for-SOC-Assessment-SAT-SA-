from pathlib import Path
from typing import Callable, Optional

from main import (
    detect_periods,
    run_multi_period_pipeline,
    run_pipeline,
    validate_dataset_at,
)

from analytics.ingestion import (
    IngestionError,
    classify_input,
    rejection_reason,
)
from analytics.validator import DatasetValidationError, ValidationReport

from application.services.ai_config import (
    AIConfig,
    create_ai_config,
)
from application.services.app_settings import AppSettings
from application.services.history_service import AssessmentRun, HistoryService

from application.paths import config_path


class AssessmentService:
    """
    Bridge between the SAT-SA desktop UI and the existing
    deterministic assessment pipeline.
    """

    def __init__(self, history: Optional[HistoryService] = None,
                  settings: Optional[AppSettings] = None):
        self.project_root = Path(__file__).resolve().parents[2]
        self.settings = settings or AppSettings()
        # Each assessment writes to its own immutable run directory.
        self.history = history or HistoryService(
            Path(self.settings.history_directory)
            if self.settings.history_directory else None)

        self.config_path = config_path()



    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def periods(self, dataset_path: str) -> list:
        """Period subdirectories, if this submission holds more than one."""
        return detect_periods(dataset_path)

    def validate(self, dataset_path: str) -> ValidationReport:
        """
        Run the "AUTOMATIC DATA VALIDATION" workflow step on its own,
        so the UI can show the result before committing to a full
        assessment run. Raises ValueError with a supervisor-readable
        message when the input is not a dataset SAT-SA can read.
        """
        self._require_supported_input(dataset_path)

        # A multi-period submission is validated on its newest period:
        # that is the one whose assessment becomes the current result,
        # and validating every period up front would delay the answer
        # the supervisor is waiting for.
        periods = self.periods(dataset_path)
        target = periods[-1][1] if len(periods) >= 2 else dataset_path
        return validate_dataset_at(target, self.settings.ingestion_limits())

    def _require_supported_input(self, dataset_path: str) -> None:
        """
        Reject an unreadable input before any work starts, with the same
        wording analytics.ingestion would use. Raising IngestionError
        rather than ValueError means the UI has exactly one failure type
        to present for every ingestion problem.
        """
        dataset = Path(dataset_path)

        if not dataset.exists():
            raise IngestionError(
                "The dataset path does not exist.",
                "Check that the submission is still on disk and readable.",
                str(dataset))

        if classify_input(str(dataset)) is None:
            raise IngestionError(rejection_reason(str(dataset)))

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------

    def run_assessment(
        self,
        dataset_path: str,
        ai_config: Optional[AIConfig] = None,
        progress_callback: Optional[
            Callable[[str], None]
        ] = None,
    ):
        self._require_supported_input(dataset_path)
        dataset = Path(dataset_path)

        if ai_config is None:
            ai_config = create_ai_config(mode="balanced")

        def progress(message: str):
            if progress_callback:
                progress_callback(message)

        periods = self.periods(str(dataset))
        multi_period = len(periods) >= 2

        # A fresh directory per run. Never reuses one, so a completed
        # assessment can never be overwritten by a later one.
        run = self.history.create_run(str(dataset), dataset.name)
        output_path = run.path

        progress("Preparing assessment...")
        if multi_period:
            progress(f"{len(periods)} submission periods detected "
                      f"({periods[0][0]} to {periods[-1][0]})")
        progress("Running SAT-SA analytics...")

        # IMPORTANT:
        # The deterministic assessment must NEVER depend on the LLM.
        # A DatasetValidationError raised here is deliberately allowed
        # to propagate: it carries the full ValidationReport, and the
        # UI renders it as a validation result rather than a crash.
        trend_report = None

        if multi_period:
            # Each period is assessed independently and the results are
            # compared. Assessing them together would corrupt every
            # per-period statistic the comparison rests on.
            result, trend_report = run_multi_period_pipeline(
                data_path=str(dataset),
                out_path=str(output_path),
                config_path=str(self.config_path),
                export_csv=self.settings.generate_csv_exports,
                export_pdf=self.settings.generate_pdf_report,
                narrate=False,
                progress_callback=progress,
            )
        else:
            result = run_pipeline(
                data_path=str(dataset),
                out_path=str(output_path),
                config_path=str(self.config_path),
                export_csv=self.settings.generate_csv_exports,
                export_pdf=self.settings.generate_pdf_report,
                narrate=False,
                progress_callback=progress,
                strict_validation=self.settings.strict_validation,
                limits=self.settings.ingestion_limits(),
            )

        if result is None:
            raise RuntimeError(
                "SAT-SA assessment pipeline returned no result."
            )

        self.history.finalise(
            run, result, trend_report=trend_report,
            ai_backend=ai_config.backend if ai_config.enabled else "")

        progress(f"Assessment saved to {run.run_id}")
        progress("Assessment completed.")

        return result, ai_config, trend_report, run
