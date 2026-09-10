from pathlib import Path
from typing import Callable, Optional

from main import run_pipeline, validate_dataset_at

from analytics.ingestion import classify_input, rejection_reason
from analytics.validator import DatasetValidationError, ValidationReport

from application.services.ai_config import (
    AIConfig,
    create_ai_config,
)


class AssessmentService:
    """
    Bridge between the SAT-SA desktop UI and the existing
    deterministic assessment pipeline.
    """

    def __init__(self):
        self.project_root = Path(__file__).resolve().parents[2]

        self.config_path = (
            self.project_root
            / "config"
            / "assessment_rules.yaml"
        )

        self.outputs_dir = self.project_root / "outputs"
        self.outputs_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, dataset_path: str) -> ValidationReport:
        """
        Run the "AUTOMATIC DATA VALIDATION" workflow step on its own,
        so the UI can show the result before committing to a full
        assessment run. Raises ValueError with a supervisor-readable
        message when the input is not a dataset SAT-SA can read.
        """
        self._require_supported_input(dataset_path)
        return validate_dataset_at(dataset_path)

    def _require_supported_input(self, dataset_path: str) -> None:
        dataset = Path(dataset_path)

        if not dataset.exists():
            raise ValueError(f"Dataset does not exist:\n{dataset}")

        if classify_input(str(dataset)) is None:
            raise ValueError(rejection_reason(str(dataset)))

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

        output_path = self.outputs_dir / "desktop_assessment"

        def progress(message: str):
            if progress_callback:
                progress_callback(message)

        progress("Preparing assessment...")
        progress("Running SAT-SA analytics...")

        # IMPORTANT:
        # The deterministic assessment must NEVER depend on the LLM.
        # A DatasetValidationError raised here is deliberately allowed
        # to propagate: it carries the full ValidationReport, and the
        # UI renders it as a validation result rather than a crash.
        result = run_pipeline(
            data_path=str(dataset),
            out_path=str(output_path),
            config_path=str(self.config_path),
            export_csv=True,
            export_pdf=True,
            narrate=False,
            progress_callback=progress,
        )

        if result is None:
            raise RuntimeError(
                "SAT-SA assessment pipeline returned no result."
            )

        progress("Assessment completed.")

        return result, ai_config
