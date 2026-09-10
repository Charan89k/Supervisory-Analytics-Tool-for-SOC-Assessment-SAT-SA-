"""
Data loader for SAT-SA.

Single responsibility: get the data into memory correctly. No analytics,
no scoring, no interpretation happens here — see validator.py and
normalizer.py for the steps that follow this one.
"""

import os
import pandas as pd

TABLES = [
    "socs", "analysts", "shifts", "alerts", "alert_events", "cases",
    "incidents", "escalations", "actions", "evidence", "telemetry",
    "policies", "mitre_techniques",
]

# Tables that are optional — a submission missing these should still load.
OPTIONAL_TABLES = {"incidents", "shifts", "mitre_techniques", "policies"}


def load_soc_dataset(path: str) -> dict:
    """
    Load every CSV table found at `path` into a dict of DataFrames,
    keyed by table name (matching the filename without extension).

    Example:
        data = load_soc_dataset("data/synthetic/")
        data["alerts"]        # DataFrame
        data["escalations"]   # DataFrame
    """
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Dataset directory not found: {path}")

    data = {}
    missing = []
    for table in TABLES:
        file_path = os.path.join(path, f"{table}.csv")
        if os.path.exists(file_path):
            data[table] = pd.read_csv(file_path)
        else:
            if table not in OPTIONAL_TABLES:
                missing.append(table)
            data[table] = pd.DataFrame()

    if missing:
        raise FileNotFoundError(
            f"Required table(s) missing from {path}: {', '.join(missing)}"
        )

    return data


def load_multiple_periods(base_path: str) -> dict:
    """
    Convenience loader for the case where each periodic submission lives
    in its own subdirectory, e.g. data/synthetic/2026-Q1/, .../2026-Q2/.
    Concatenates each table across all subdirectories found.

    Returns the same shape as load_soc_dataset: dict of table -> DataFrame.
    """
    subdirs = sorted(
        d for d in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, d))
    )
    if not subdirs:
        return load_soc_dataset(base_path)

    combined = {table: [] for table in TABLES}
    for sub in subdirs:
        period_data = load_soc_dataset(os.path.join(base_path, sub))
        for table in TABLES:
            if not period_data[table].empty:
                period_data[table]["_submission_period"] = sub
                combined[table].append(period_data[table])

    return {
        table: (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())
        for table, frames in combined.items()
    }
