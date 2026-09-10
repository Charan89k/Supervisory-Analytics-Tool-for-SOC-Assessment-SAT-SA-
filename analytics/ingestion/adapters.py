"""
Format adapters. Each resolves one input shape into the normalized
internal model: {table_name: DataFrame}.

Adapters parse data and nothing else. No adapter imports a module named
in a submission, evaluates a field, or resolves a reference a submission
supplies. `pandas.read_csv` and `json.load` are pure parsers; the SQLite
reader opens read-only and never loads an extension. There is no code
path by which dataset contents become executable.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, Optional

import pandas as pd

from analytics.ingestion.errors import IngestionError
from analytics.ingestion.limits import DEFAULT_LIMITS, IngestionLimits, human_bytes
from analytics.ingestion.schema import TABLES, missing_required

Dataset = Dict[str, pd.DataFrame]


def _empty_dataset() -> Dataset:
    return {table: pd.DataFrame() for table in TABLES}


def _require_tables(dataset: Dataset, found, source: str) -> Dataset:
    """
    Fail only when a required table has no source at all.

    A table that exists but holds zero rows is deliberately NOT an
    ingestion failure. The validator's contract is to report every
    problem with a submission at once, so a supervisor can send one
    complete list of corrections back to the entity; raising here on an
    empty table would short-circuit that into "first problem only".
    Absent-entirely is different — there is nothing to hand the
    validator, not even column names.
    """
    absent = missing_required(found)
    if absent:
        raise IngestionError(
            f"The submission is missing required table(s): "
            f"{', '.join(absent)}.",
            "Each required table must be present. Optional tables "
            "(incidents, shifts, policies, mitre_techniques) may be "
            "omitted.",
            source)
    return dataset


def _check_file_size(path: str, limits: IngestionLimits) -> None:
    size = os.path.getsize(path)
    if size > limits.max_file_bytes:
        raise IngestionError(
            f"The file is {human_bytes(size)}, above the "
            f"{human_bytes(limits.max_file_bytes)} limit.",
            source=path)


# ---------------------------------------------------------------------------
# CSV directory — the original format
# ---------------------------------------------------------------------------

def read_csv_directory(path: str,
                        limits: IngestionLimits = DEFAULT_LIMITS) -> Dataset:
    dataset = _empty_dataset()
    found = []

    for table in TABLES:
        file_path = os.path.join(path, f"{table}.csv")
        if not os.path.isfile(file_path):
            continue

        found.append(table)
        _check_file_size(file_path, limits)

        try:
            dataset[table] = pd.read_csv(file_path)
        except pd.errors.EmptyDataError:
            dataset[table] = pd.DataFrame()
        except UnicodeDecodeError:
            raise IngestionError(
                f"'{table}.csv' is not valid UTF-8 text.",
                "Re-export the table as UTF-8 encoded CSV.",
                file_path)
        except pd.errors.ParserError as exc:
            raise IngestionError(
                f"'{table}.csv' is malformed and could not be parsed: {exc}",
                "Check for unescaped quotes or inconsistent column counts.",
                file_path)

    return _require_tables(dataset, found, path)


# ---------------------------------------------------------------------------
# JSON — a single bundle, or a folder of per-table files
# ---------------------------------------------------------------------------

def _frame_from_records(records, table: str, source: str) -> pd.DataFrame:
    if records is None:
        return pd.DataFrame()
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise IngestionError(
            f"Table '{table}' must be a list of records, "
            f"but is {type(records).__name__}.",
            "Each table should be a JSON array of row objects.",
            source)
    if records and not all(isinstance(row, dict) for row in records):
        raise IngestionError(
            f"Table '{table}' contains entries that are not objects.",
            "Each row must be a JSON object of column/value pairs.",
            source)
    return pd.DataFrame(records)


def _load_json(path: str, limits: IngestionLimits):
    _check_file_size(path, limits)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except UnicodeDecodeError:
        raise IngestionError(
            "The file is not valid UTF-8 text.",
            "Re-export the submission as UTF-8 encoded JSON.", path)
    except json.JSONDecodeError as exc:
        raise IngestionError(
            f"The file is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno}).",
            "Check that the submission was not truncated in transfer.",
            path)


def read_json_bundle(path: str,
                      limits: IngestionLimits = DEFAULT_LIMITS) -> Dataset:
    """One JSON file holding every table: {"alerts": [...], "cases": [...]}."""
    payload = _load_json(path, limits)

    if not isinstance(payload, dict):
        raise IngestionError(
            "A JSON submission must be an object mapping table names to "
            f"rows, but this file contains a {type(payload).__name__}.",
            'Expected shape: {"alerts": [...], "cases": [...], ...}',
            path)

    # Tolerate one level of wrapping, which database exporters commonly add.
    for wrapper in ("tables", "data"):
        if wrapper in payload and isinstance(payload[wrapper], dict):
            payload = payload[wrapper]
            break

    dataset = _empty_dataset()
    found = []
    for table in TABLES:
        if table in payload:
            found.append(table)
            dataset[table] = _frame_from_records(payload[table], table, path)

    return _require_tables(dataset, found, path)


def read_json_directory(path: str,
                         limits: IngestionLimits = DEFAULT_LIMITS) -> Dataset:
    """A folder of alerts.json, cases.json, ... each a JSON array."""
    dataset = _empty_dataset()
    found = []

    for table in TABLES:
        file_path = os.path.join(path, f"{table}.json")
        if not os.path.isfile(file_path):
            continue
        found.append(table)
        dataset[table] = _frame_from_records(
            _load_json(file_path, limits), table, file_path)

    return _require_tables(dataset, found, path)


# ---------------------------------------------------------------------------
# SQLite — the practical offline "database export"
# ---------------------------------------------------------------------------

def read_sqlite(path: str,
                 limits: IngestionLimits = DEFAULT_LIMITS) -> Dataset:
    """
    Read a SQLite export. Opened READ-ONLY through a file: URI, so the
    submission cannot be modified by the act of assessing it, and with
    extension loading left disabled (its sqlite3 default) so a crafted
    database cannot load a shared library.
    """
    _check_file_size(path, limits)

    try:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise IngestionError(
            f"The database could not be opened: {exc}",
            "Confirm the file is a SQLite database export.", path)

    dataset = _empty_dataset()
    try:
        present = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }

        found = []
        for table in TABLES:
            if table not in present:
                continue
            found.append(table)
            # Table name comes from our own TABLES allow-list, never from
            # the file, so it cannot carry injected SQL.
            dataset[table] = pd.read_sql_query(
                f'SELECT * FROM "{table}"', connection)

    except (sqlite3.DatabaseError, pd.errors.DatabaseError) as exc:
        raise IngestionError(
            f"The database could not be read: {exc}",
            "The file may be corrupt or not a SQLite database.", path)
    finally:
        connection.close()

    return _require_tables(dataset, found, path)


# ---------------------------------------------------------------------------
# Directory dispatch
# ---------------------------------------------------------------------------

def read_directory(path: str,
                    limits: IngestionLimits = DEFAULT_LIMITS) -> Dataset:
    """
    Read a folder, choosing the format from what it actually contains.
    CSV wins when both are present: it is the documented primary format,
    and a folder holding both is more likely to be a partial re-export
    than a deliberate mix.
    """
    entries = os.listdir(path)

    if any(e.lower().endswith(".csv") for e in entries):
        return read_csv_directory(path, limits)

    if any(e.lower().endswith(".json") for e in entries):
        return read_json_directory(path, limits)

    single = [e for e in entries
              if e.lower().endswith((".sqlite", ".db", ".sqlite3"))]
    if len(single) == 1:
        return read_sqlite(os.path.join(path, single[0]), limits)

    raise IngestionError(
        "The folder contains no CSV, JSON, or database files.",
        "A dataset folder should contain the SOC tables as CSV "
        "(alerts.csv, cases.csv, ...) or JSON.",
        path)
