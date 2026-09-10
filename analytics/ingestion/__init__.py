"""
Dataset ingestion for SAT-SA.

This package is the single place that decides what a "dataset" is
allowed to be. Everything else — the loader, the desktop drop zone,
the CLI — asks here rather than deciding for itself.

That matters because the two used to disagree: the desktop drop zone
advertised "CSV / JSON / ZIP / DATA FOLDER" and filtered for
`*.zip *.csv *.json`, while the loader only ever accepted a directory
of CSVs. Selecting a file produced a hard FileNotFoundError with no
usable explanation. A capability claim that lives in one module and a
capability implementation that lives in another will drift; keeping
the claim next to the implementation is what stops that.

Phase 2 adds the real adapters (ZIP, JSON, single-file bundles,
database exports) behind `resolve_dataset()`. Until an adapter exists
and is tested, its format does NOT appear in SUPPORTED_INPUTS — the UI
will never offer a format the engine cannot actually read.
"""

import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class InputFormat:
    """One dataset shape SAT-SA can actually ingest today."""

    key: str
    label: str
    description: str
    #: Qt file-dialog glob patterns; empty means "directory, not file".
    patterns: tuple = ()
    is_directory: bool = False


#: Formats that are implemented AND tested. Phase 2 extends this list.
SUPPORTED_INPUTS: List[InputFormat] = [
    InputFormat(
        key="csv_directory",
        label="Dataset folder (CSV tables)",
        description=(
            "A folder containing the SOC submission as CSV files "
            "(alerts.csv, cases.csv, escalations.csv, telemetry.csv, ...)."
        ),
        is_directory=True,
    ),
]

#: Declared in the official PS data environment, not yet implemented.
#: Listed separately so the UI can say "coming" without pretending.
PLANNED_INPUTS = ("ZIP archive", "JSON submission", "Database export")


def describe_supported_inputs() -> str:
    """One-line, user-facing statement of what can be loaded right now."""
    return " · ".join(fmt.label.upper() for fmt in SUPPORTED_INPUTS)


def dialog_filter() -> str:
    """Qt getOpenFileName filter string covering file-based formats only."""
    parts = []
    for fmt in SUPPORTED_INPUTS:
        if fmt.patterns:
            parts.append(f"{fmt.label} ({' '.join(fmt.patterns)})")
    return ";;".join(parts) if parts else ""


def accepts_files() -> bool:
    """True once at least one file-based (non-directory) format exists."""
    return any(not fmt.is_directory for fmt in SUPPORTED_INPUTS)


def classify_input(path: str) -> Optional[InputFormat]:
    """Return the InputFormat that handles `path`, or None if unsupported."""
    if os.path.isdir(path):
        return next((f for f in SUPPORTED_INPUTS if f.is_directory), None)

    lowered = path.lower()
    for fmt in SUPPORTED_INPUTS:
        for pattern in fmt.patterns:
            if lowered.endswith(pattern.lstrip("*")):
                return fmt
    return None


def rejection_reason(path: str) -> str:
    """
    A supervisor-readable explanation of why `path` cannot be loaded.
    Never leaks a stack trace or a bare OSError.
    """
    if not os.path.exists(path):
        return f"Path does not exist:\n{path}"

    if os.path.isfile(path):
        suffix = os.path.splitext(path)[1].lower() or "(no extension)"
        planned = ", ".join(PLANNED_INPUTS)
        return (
            f"SAT-SA cannot yet read a single {suffix} file as a dataset.\n\n"
            f"Supported now: {describe_supported_inputs()}.\n"
            f"Planned: {planned}.\n\n"
            "Select the folder that contains the CSV tables instead."
        )

    return f"Unsupported dataset input:\n{path}"
