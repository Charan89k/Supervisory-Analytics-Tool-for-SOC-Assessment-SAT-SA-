"""
Dataset ingestion for SAT-SA.

This package is the single place that decides what a "dataset" is, and
the only place that turns an external submission into the normalized
internal model the analytics engine consumes:

    resolve_dataset(path) -> {table_name: DataFrame}

Everything else — the loader, the desktop drop zone, the CLI — asks
here rather than deciding for itself. That matters because the two used
to disagree: the drop zone advertised "CSV / JSON / ZIP / DATA FOLDER"
while the loader accepted only a directory of CSVs, so selecting a file
produced a raw FileNotFoundError. A capability claim that lives in one
module and its implementation in another will drift, so SUPPORTED_INPUTS
below is derived from what is actually implemented and tested.

Submissions are untrusted input. Archives are inspected before a byte is
written (see safe_archive), files are size-bounded, malformed content is
reported as a supervisor-readable message, and nothing in a submission
is ever executed.
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from analytics.ingestion.adapters import (
    Dataset,
    read_csv_directory,
    read_directory,
    read_json_bundle,
    read_json_directory,
    read_sqlite,
)
from analytics.ingestion.errors import IngestionError, UnsafeArchiveError
from analytics.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from analytics.ingestion.safe_archive import extract_to_temp, inspect_archive
from analytics.ingestion.schema import (
    OPTIONAL_TABLES,
    REQUIRED_TABLES,
    TABLES,
    missing_required,
)

__all__ = [
    "Dataset", "DEFAULT_LIMITS", "IngestionError", "IngestionLimits",
    "OPTIONAL_TABLES", "PLANNED_INPUTS", "REQUIRED_TABLES", "SUPPORTED_INPUTS",
    "TABLES", "UnsafeArchiveError", "accepts_files", "classify_input",
    "describe_supported_inputs", "dialog_filter", "inspect_archive",
    "missing_required", "rejection_reason", "resolve_dataset",
]


@dataclass(frozen=True)
class InputFormat:
    """One dataset shape SAT-SA can actually ingest today."""

    key: str
    label: str
    description: str
    #: File-dialog glob patterns; empty means "directory, not file".
    patterns: tuple = ()
    is_directory: bool = False


#: Formats that are implemented AND tested. A format does not appear
#: here until both are true.
SUPPORTED_INPUTS: List[InputFormat] = [
    InputFormat(
        key="directory",
        label="Dataset folder",
        description=(
            "A folder holding the SOC tables as CSV or JSON files "
            "(alerts.csv, cases.csv, escalations.csv, ...)."
        ),
        is_directory=True,
    ),
    InputFormat(
        key="zip",
        label="ZIP archive",
        description=(
            "A zipped submission containing the SOC tables. Inspected "
            "for unsafe paths and expansion limits before extraction."
        ),
        patterns=("*.zip",),
    ),
    InputFormat(
        key="json",
        label="JSON submission",
        description=(
            'A single JSON file mapping table names to rows: '
            '{"alerts": [...], "cases": [...]}.'
        ),
        patterns=("*.json",),
    ),
    InputFormat(
        key="sqlite",
        label="Database export (SQLite)",
        description=(
            "A SQLite database export with one table per SOC table. "
            "Opened read-only."
        ),
        patterns=("*.sqlite", "*.db", "*.sqlite3"),
    ),
]

#: Named in the official PS data environment but not implemented.
PLANNED_INPUTS = ("Live database connections", "Local API exports")


def describe_supported_inputs() -> str:
    """User-facing statement of what can be loaded right now."""
    return " · ".join(fmt.label.upper() for fmt in SUPPORTED_INPUTS)


def dialog_filter() -> str:
    """Qt getOpenFileName filter covering the file-based formats."""
    parts = []
    all_patterns = []
    for fmt in SUPPORTED_INPUTS:
        if fmt.patterns:
            parts.append(f"{fmt.label} ({' '.join(fmt.patterns)})")
            all_patterns.extend(fmt.patterns)
    if not parts:
        return ""
    return ";;".join(
        [f"Dataset files ({' '.join(all_patterns)})"] + parts + ["All Files (*)"]
    )


def accepts_files() -> bool:
    """True when at least one file-based (non-directory) format exists."""
    return any(not fmt.is_directory for fmt in SUPPORTED_INPUTS)


def classify_input(path: str) -> Optional[InputFormat]:
    """Return the InputFormat handling `path`, or None if unsupported."""
    if os.path.isdir(path):
        return next((f for f in SUPPORTED_INPUTS if f.is_directory), None)

    lowered = path.lower()
    for fmt in SUPPORTED_INPUTS:
        for pattern in fmt.patterns:
            if lowered.endswith(pattern.lstrip("*")):
                return fmt
    return None


def rejection_reason(path: str) -> str:
    """Supervisor-readable explanation of why `path` cannot be loaded."""
    if not os.path.exists(path):
        return f"Path does not exist:\n{path}"

    if os.path.isfile(path):
        suffix = os.path.splitext(path)[1].lower() or "(no extension)"
        return (
            f"SAT-SA cannot read a {suffix} file as a dataset.\n\n"
            f"Supported: {describe_supported_inputs()}.\n\n"
            "If this is one table of a submission, select the folder "
            "that contains all of the tables instead."
        )

    return f"Unsupported dataset input:\n{path}"


@contextmanager
def _extracted(path: str, limits: IngestionLimits):
    """Extract an archive to a temp dir, always cleaning the root up."""
    temp_root, dataset_dir = extract_to_temp(path, limits)
    try:
        yield dataset_dir
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def resolve_dataset(path: str,
                     limits: IngestionLimits = DEFAULT_LIMITS) -> Dataset:
    """
    Turn any supported input into the normalized internal model.

    Raises IngestionError — never a bare OSError, KeyError, or parser
    exception — so every caller has one failure type to present.
    """
    if not os.path.exists(path):
        raise IngestionError(
            "The dataset path does not exist.",
            "Check that the submission is still on disk and readable.",
            path)

    fmt = classify_input(path)
    if fmt is None:
        raise IngestionError(rejection_reason(path), source="")

    try:
        if fmt.key == "directory":
            return read_directory(path, limits)

        if fmt.key == "zip":
            with _extracted(path, limits) as directory:
                return read_directory(directory, limits)

        if fmt.key == "json":
            return read_json_bundle(path, limits)

        if fmt.key == "sqlite":
            return read_sqlite(path, limits)

    except IngestionError:
        raise
    except MemoryError:
        raise IngestionError(
            "The submission is too large to load into memory on this "
            "machine.",
            "Split the submission by entity or assessment period.",
            path)
    except OSError as exc:
        raise IngestionError(
            f"The dataset could not be read: {exc}",
            "Check file permissions and that the media is readable.",
            path)

    raise IngestionError(rejection_reason(path), source=path)
