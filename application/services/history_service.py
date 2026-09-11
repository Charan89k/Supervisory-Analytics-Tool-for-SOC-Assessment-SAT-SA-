"""
Assessment history.

Every completed assessment gets its own timestamped, immutable
directory. Before this the desktop application wrote every run to a
single `outputs/desktop_assessment`, so a second assessment destroyed
the first — a supervisor who ran one assessment, then another, lost the
first one's findings, evidence exports and PDF with no warning. That is
data loss, not a missing convenience.

Immutability is the property that matters. A supervisory assessment is
a record: it may be referred back to, compared against a later period,
or produced as the basis for a finding. A run directory is created
once, written once, and never reopened for writing — `create_run`
refuses to return a path that already exists.

Each run carries a small `run.json` summary so the history list can be
built without parsing a 1 MB assessment_results.json per run.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from application.paths import default_history_root

#: Written inside each run directory.
SUMMARY_FILE = "run.json"

#: Override for tests and for a packaged application whose data
#: directory is not the project tree.
ENV_HISTORY_DIR = "SATSA_ASSESSMENTS_DIR"

RUN_ID_FORMAT = "%Y-%m-%d_%H%M%S"


@dataclass
class RunSummary:
    """What the history list shows without opening the full results."""

    run_id: str
    started_at: str = ""
    completed_at: str = ""
    dataset_path: str = ""
    dataset_label: str = ""
    entities: int = 0
    total_findings: int = 0
    execution_gaps: int = 0
    negative_space: int = 0
    review_queue: int = 0
    total_alerts: int = 0
    periods: List[str] = field(default_factory=list)
    ai_backend: str = ""
    ai_explained: int = 0
    validation_issues: int = 0

    @property
    def multi_period(self) -> bool:
        return len(self.periods) > 1

    def label(self) -> str:
        when = self.started_at.replace("T", " ")[:16] or self.run_id
        scope = (f"{len(self.periods)} periods" if self.multi_period
                 else "single period")
        return (f"{when} — {self.dataset_label or 'dataset'} "
                f"({self.entities} entities, {self.total_findings:,} findings, "
                f"{scope})")


@dataclass
class AssessmentRun:
    summary: RunSummary
    path: Path

    @property
    def run_id(self) -> str:
        return self.summary.run_id

    def artifact(self, name: str) -> Path:
        return self.path / name

    def results_path(self) -> Path:
        return self.path / "assessment_results.json"

    def exists(self) -> bool:
        return self.results_path().is_file()

    def period_directories(self):
        """
        (label, path) for each per-period output directory, oldest
        first; empty for a single-period run.

        A multi-period assessment writes its cross-period artifacts at
        the run root and each period's own outputs under periods/<name>/.
        Anything that lists a run's outputs has to know that, so the
        knowledge lives here with the rest of the run layout rather than
        being re-derived by each caller.
        """
        root = self.path / "periods"
        if not root.is_dir():
            return []
        return [(entry.name, entry)
                for entry in sorted(root.iterdir())
                if entry.is_dir() and any(entry.iterdir())]

    def artifact_directories(self):
        """
        Every directory holding artifacts for this run, labelled.

        The run root first, then one entry per period. A caller that
        walks this list cannot miss an output the pipeline wrote.
        """
        return [(None, self.path)] + list(self.period_directories())


class HistoryService:
    """Creates, lists and loads assessment runs."""

    def __init__(self, root: Optional[Path] = None):
        override = os.environ.get(ENV_HISTORY_DIR)
        if root is not None:
            self.root = Path(root)
        elif override:
            self.root = Path(override)
        else:
            # Never derived from this file's location: frozen, that is
            # the bundle's temporary unpack directory, and every stored
            # assessment would vanish when the application closes.
            self.root = default_history_root()

    # -- creating ----------------------------------------------------

    def create_run(self, dataset_path: str = "",
                    dataset_label: str = "") -> AssessmentRun:
        """
        Allocate a fresh, empty run directory.

        Never returns a path that already exists. Two assessments
        started within the same second get `-2`, `-3` and so on, rather
        than the second quietly overwriting the first.
        """
        started = datetime.now()
        base = started.strftime(RUN_ID_FORMAT)

        run_id, suffix = base, 1
        while (self.root / run_id).exists():
            suffix += 1
            run_id = f"{base}-{suffix}"

        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=False)

        summary = RunSummary(
            run_id=run_id,
            started_at=started.isoformat(timespec="seconds"),
            dataset_path=str(dataset_path),
            dataset_label=dataset_label or Path(dataset_path).name,
        )
        return AssessmentRun(summary=summary, path=path)

    def finalise(self, run: AssessmentRun, results: dict,
                  trend_report=None, ai_backend: str = "") -> AssessmentRun:
        """
        Write the run summary once the assessment has completed.

        Derived from the results rather than passed in, so the history
        list cannot disagree with the assessment it describes.
        """
        summary = run.summary
        summary.completed_at = datetime.now().isoformat(timespec="seconds")

        metadata = (results or {}).get("run_metadata", {}) or {}
        entities = (results or {}).get("entities", []) or []
        queue = (results or {}).get("review_queue", []) or []

        summary.entities = len(entities)
        summary.total_alerts = int(metadata.get("total_alerts", 0))
        summary.total_findings = int(metadata.get("total_findings", 0))
        summary.execution_gaps = sum(
            len(e.get("execution_gap_findings", [])) for e in entities)
        summary.negative_space = sum(
            len(e.get("negative_space_findings", [])) for e in entities)
        summary.review_queue = len(queue)
        summary.ai_backend = ai_backend
        summary.ai_explained = sum(
            1 for record in queue
            if record.get("narration_source") not in (None, "rule"))
        summary.validation_issues = int(
            ((results or {}).get("validation_report", {}) or {})
            .get("issue_count", 0))

        if trend_report is not None and getattr(trend_report, "periods", None):
            summary.periods = list(trend_report.periods)
        elif metadata.get("submission_period_labels"):
            summary.periods = list(metadata["submission_period_labels"])

        with open(run.path / SUMMARY_FILE, "w", encoding="utf-8") as handle:
            json.dump(asdict(summary), handle, indent=2, sort_keys=True)

        return run

    # -- listing -----------------------------------------------------

    def list_runs(self) -> List[AssessmentRun]:
        """
        Completed runs, newest first.

        A directory without a readable summary is skipped rather than
        guessed at: it is an interrupted or corrupted run, and inventing
        a summary for it would put a fiction in the history.
        """
        if not self.root.is_dir():
            return []

        runs = []
        for entry in sorted(self.root.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            summary = self._read_summary(entry)
            if summary is None:
                continue
            runs.append(AssessmentRun(summary=summary, path=entry))

        runs.sort(key=lambda r: r.summary.started_at, reverse=True)
        return runs

    def _read_summary(self, path: Path) -> Optional[RunSummary]:
        try:
            with open(path / SUMMARY_FILE, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError,
                UnicodeDecodeError):
            return None

        if not isinstance(stored, dict):
            return None

        known = {f for f in asdict(RunSummary(run_id=""))}
        filtered = {k: v for k, v in stored.items() if k in known}
        filtered.setdefault("run_id", path.name)
        try:
            return RunSummary(**filtered)
        except TypeError:
            return None

    def get(self, run_id: str) -> Optional[AssessmentRun]:
        path = self.root / run_id
        summary = self._read_summary(path) if path.is_dir() else None
        return AssessmentRun(summary=summary, path=path) if summary else None

    def load_results(self, run: AssessmentRun) -> Optional[dict]:
        try:
            with open(run.results_path(), encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError,
                UnicodeDecodeError):
            return None

    def latest(self) -> Optional[AssessmentRun]:
        runs = self.list_runs()
        return runs[0] if runs else None

    # -- removing ----------------------------------------------------

    def delete(self, run: AssessmentRun) -> bool:
        """
        Remove one run. The only operation that destroys an assessment,
        and it is never automatic — history is not pruned on a schedule
        or by a retention limit, because a supervisor deciding an old
        assessment no longer matters is a supervisory decision.
        """
        try:
            shutil.rmtree(run.path)
            return True
        except OSError:
            return False
