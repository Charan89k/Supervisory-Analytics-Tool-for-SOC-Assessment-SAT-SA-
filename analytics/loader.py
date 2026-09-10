"""
Data loader for SAT-SA.

Single responsibility: get the data into memory correctly. No analytics,
no scoring, no interpretation happens here — see validator.py and
normalizer.py for the steps that follow this one.

Format handling now lives in `analytics.ingestion`, which resolves any
supported submission (CSV folder, JSON, ZIP, SQLite export) into the
same {table_name: DataFrame} model. This module keeps the entry points
the pipeline has always called.
"""

import os

import pandas as pd

from analytics.ingestion import (
    DEFAULT_LIMITS,
    IngestionError,
    OPTIONAL_TABLES,
    TABLES,
    resolve_dataset,
)

__all__ = ["TABLES", "OPTIONAL_TABLES", "load_soc_dataset",
           "load_multiple_periods"]


def load_soc_dataset(path: str, limits=DEFAULT_LIMITS) -> dict:
    """
    Load a submission into a dict of DataFrames keyed by table name.

    Example:
        data = load_soc_dataset("data/synthetic/")
        data["alerts"]        # DataFrame
        data["escalations"]   # DataFrame

    Accepts a folder of CSV or JSON tables, a ZIP archive, a single JSON
    submission, or a SQLite export. Raises IngestionError with a
    supervisor-readable message if the submission cannot be read.
    """
    return resolve_dataset(path, limits)


def load_multiple_periods(base_path: str, limits=DEFAULT_LIMITS) -> dict:
    """
    Loader for the case where each periodic submission lives in its own
    subdirectory, e.g. data/synthetic/2026-Q1/, .../2026-Q2/.
    Concatenates each table across all subdirectories found, tagging
    every row with the period it came from.

    The `_submission_period` column is what makes trend analysis across
    time periods possible without inventing history: it records the
    period each row genuinely belongs to.

    Returns the same shape as load_soc_dataset.
    """
    if not os.path.isdir(base_path):
        raise IngestionError(
            "Multi-period loading requires a folder of period "
            "subdirectories.",
            "Each subdirectory should hold one submission, named for "
            "its period (for example 2026-Q1).",
            base_path)

    subdirs = sorted(
        d for d in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, d))
    )
    if not subdirs:
        return load_soc_dataset(base_path, limits)

    combined = {table: [] for table in TABLES}
    for sub in subdirs:
        period_data = load_soc_dataset(os.path.join(base_path, sub), limits)
        for table in TABLES:
            frame = period_data.get(table)
            if frame is not None and not frame.empty:
                frame = frame.copy()
                frame["_submission_period"] = sub
                combined[table].append(frame)

    return {
        table: (pd.concat(frames, ignore_index=True) if frames
                else pd.DataFrame())
        for table, frames in combined.items()
    }
