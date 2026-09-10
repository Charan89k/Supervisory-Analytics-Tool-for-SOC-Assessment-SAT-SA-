"""
The normalized internal data model every ingestion adapter produces.

A SAT-SA dataset is a dict of {table_name: DataFrame}. Every adapter —
CSV folder, JSON submission, ZIP archive, SQLite export — resolves to
exactly this shape, so the analytics engine never learns which format a
submission arrived in.
"""

#: Canonical tables, in the order a supervisor would think about them.
TABLES = [
    "socs", "analysts", "shifts", "alerts", "alert_events", "cases",
    "incidents", "escalations", "actions", "evidence", "telemetry",
    "policies", "mitre_techniques",
]

#: Tables a submission may legitimately omit. Everything else is
#: required, and its absence is reported as a dataset error rather than
#: silently producing an empty analysis.
OPTIONAL_TABLES = {"incidents", "shifts", "mitre_techniques", "policies"}

REQUIRED_TABLES = [t for t in TABLES if t not in OPTIONAL_TABLES]


def missing_required(present) -> list:
    """Which required tables are absent from an iterable of table names."""
    have = set(present)
    return [t for t in REQUIRED_TABLES if t not in have]
