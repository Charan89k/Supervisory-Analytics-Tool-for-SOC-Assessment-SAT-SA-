"""
Validator for SAT-SA.

Single responsibility: confirm the loaded data is structurally sound
before any analytics touches it. Produces a list of ValidationIssue
objects rather than raising — a supervisor should see a full report of
what's wrong with a submission, not just the first problem found.
"""

from dataclasses import dataclass, field
from typing import List

REQUIRED_COLUMNS = {
    "socs": ["soc_id", "organization_name", "sector"],
    "alerts": ["alert_id", "soc_id", "timestamp_created", "severity",
               "assigned_analyst_id", "status"],
    "alert_events": ["event_id", "alert_id", "event_type", "timestamp"],
    "cases": ["case_id", "soc_id", "created_at", "severity", "status", "closed_at"],
    "escalations": ["escalation_id", "alert_id", "required", "initiated"],
    "actions": ["action_id", "alert_id", "analyst_id", "timestamp", "action_type"],
    "evidence": ["evidence_id", "alert_id"],
    "telemetry": ["telemetry_id", "soc_id", "expected", "enabled", "coverage_percentage"],
}

VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


@dataclass
class ValidationIssue:
    table: str
    severity: str  # "ERROR" (blocks analytics) or "WARNING" (proceed with caution)
    message: str
    row_count: int = 0


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    def add(self, table, severity, message, row_count=0):
        self.issues.append(ValidationIssue(table, severity, message, row_count))

    def summary(self) -> str:
        lines = [f"Validation report — {len(self.issues)} issue(s) found"]
        for i in self.issues:
            lines.append(f"  [{i.severity}] {i.table}: {i.message}"
                         + (f" ({i.row_count} rows)" if i.row_count else ""))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """The shape main.py embeds in assessment_results.json."""
        return {
            "issue_count": len(self.issues),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [
                {"table": i.table, "severity": i.severity,
                 "message": i.message, "row_count": i.row_count}
                for i in self.issues
            ],
        }


class DatasetValidationError(Exception):
    """
    Raised when a dataset fails ERROR-level validation and the pipeline
    cannot proceed.

    Carries the full ValidationReport rather than just a message, so a
    caller (notably the desktop UI) can show the supervisor every issue
    found — which table, which severity, how many rows — instead of a
    bare "assessment failed". The official PS requires the validation
    result to be a visible step in the workflow, not a log line.
    """

    def __init__(self, report: "ValidationReport", data_path: str = ""):
        self.report = report
        self.data_path = data_path
        super().__init__(self.message())

    def message(self) -> str:
        errors = self.report.errors
        head = (f"Dataset validation failed — {len(errors)} blocking "
                f"error(s) must be corrected before an assessment can run.")
        if self.data_path:
            head += f"\nDataset: {self.data_path}"
        return head + "\n\n" + self.report.summary()


def validate_dataset(data: dict) -> ValidationReport:
    report = ValidationReport()

    for table, required_cols in REQUIRED_COLUMNS.items():
        df = data.get(table)
        if df is None or df.empty:
            report.add(table, "ERROR", "table is missing or empty")
            continue
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            report.add(table, "ERROR", f"missing required column(s): {missing_cols}")

    alerts = data.get("alerts")
    if alerts is not None and not alerts.empty:
        bad_severity = alerts[~alerts["severity"].isin(VALID_SEVERITIES)]
        if not bad_severity.empty:
            report.add("alerts", "WARNING", "unrecognized severity value(s)", len(bad_severity))

        dup_ids = alerts[alerts.duplicated("alert_id")]
        if not dup_ids.empty:
            report.add("alerts", "ERROR", "duplicate alert_id values found", len(dup_ids))

        # referential integrity: every alert should belong to a known SOC
        socs = data.get("socs")
        if socs is not None and not socs.empty:
            orphan_alerts = alerts[~alerts["soc_id"].isin(socs["soc_id"])]
            if not orphan_alerts.empty:
                report.add("alerts", "ERROR",
                            "alerts reference soc_id not present in socs table",
                            len(orphan_alerts))

    escalations = data.get("escalations")
    if escalations is not None and not escalations.empty and alerts is not None:
        orphan_esc = escalations[~escalations["alert_id"].isin(alerts["alert_id"])]
        if not orphan_esc.empty:
            report.add("escalations", "WARNING",
                        "escalation rows reference unknown alert_id", len(orphan_esc))

    telemetry = data.get("telemetry")
    if telemetry is not None and not telemetry.empty:
        bad_coverage = telemetry[
            (telemetry["coverage_percentage"] < 0) | (telemetry["coverage_percentage"] > 100)
        ]
        if not bad_coverage.empty:
            report.add("telemetry", "WARNING",
                        "coverage_percentage outside 0-100 range", len(bad_coverage))

    return report
