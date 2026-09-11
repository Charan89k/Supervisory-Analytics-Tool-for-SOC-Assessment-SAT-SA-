# SAT-SA — Data Formats

## Accepted submissions

| Format | Shape | Notes |
|---|---|---|
| **Dataset folder** | a directory of `alerts.csv`, `cases.csv`, … | CSV wins if both CSV and JSON are present |
| **ZIP archive** | the same tables, zipped | inspected before extraction; a single wrapper folder is descended automatically |
| **JSON submission** | one file: `{"alerts": [...], "cases": [...]}` | one level of `{"tables": …}` wrapping is tolerated |
| **JSON folder** | `alerts.json`, `cases.json`, … each an array | |
| **SQLite export** | one table per SOC table | opened **read-only**; assessing never modifies the file |

Every format resolves to the same internal model, verified by test: all
five produce a byte-identical assessment fingerprint from the same
source data.

**Not implemented:** live database connections and local API exports.
They are named in `PLANNED_INPUTS` and never offered by the UI — the
file dialog is built from the adapter registry, so it cannot advertise a
format the engine does not implement.

## Internal model

A dataset is `{table_name: DataFrame}`. Thirteen tables:

| Table | Required | Key columns |
|---|---|---|
| `socs` | yes | `soc_id`, `organization_name`, `sector` |
| `analysts` | yes | `analyst_id`, `soc_id` |
| `alerts` | yes | `alert_id`, `soc_id`, `timestamp_created`, `severity`, `assigned_analyst_id`, `status`, `asset_id`, `category` |
| `alert_events` | yes | `event_id`, `alert_id`, `event_type`, `timestamp` |
| `cases` | yes | `case_id`, `soc_id`, `alert_id`, `created_at`, `closed_at`, `resolution_reason`, `investigation_notes` |
| `escalations` | yes | `escalation_id`, `alert_id`, `required`, `initiated` |
| `actions` | yes | `action_id`, `alert_id`, `analyst_id`, `timestamp`, `action_type` |
| `evidence` | yes | `evidence_id`, `alert_id` |
| `telemetry` | yes | `telemetry_id`, `soc_id`, `expected`, `enabled`, `coverage_percentage` |
| `incidents` | optional | |
| `shifts` | optional | |
| `policies` | optional | |
| `mitre_techniques` | optional | |

A **required table with no source** is an ingestion error. A required
table that is **present but empty** is not — that is a validation
concern, and the validator's contract is to report every problem at once
so a supervisor can send one complete correction list back.

`alert_events.event_type` drives most timing analysis:
`CREATED`, `ACKNOWLEDGED`, `INVESTIGATION_STARTED`, `CLOSED`, `REOPENED`.

## Normalisation

`build_alerts_enriched` produces the single flat join every detector
reads, adding:

| Column | Derived from |
|---|---|
| `ack_delay_minutes` | CREATED → ACKNOWLEDGED |
| `investigation_duration_minutes` | INVESTIGATION_STARTED → first CLOSED |
| `was_acknowledged`, `investigation_started`, `was_closed`, `was_reopened` | presence of the event |
| `has_evidence` | any evidence row for the alert |
| `escalation_required`, `escalation_initiated`, `escalation_delay_minutes` | the escalation record |
| `case_id` | the case for the alert |

Event **presence** flags are separate from durations on purpose. A
duration is null both when an alert was never investigated and when its
timestamps are unusable, so a duration alone cannot distinguish "no
investigation happened" from "we cannot tell" —
`ACK_WITHOUT_INVESTIGATION` turns on exactly that distinction.

## Multi-period submissions

A folder whose subdirectories each look like a dataset is treated as
multiple periods:

```
submission/
  2026-Q1/   alerts.csv …
  2026-Q2/   alerts.csv …
  2026-Q3/   alerts.csv …
```

A subdirectory qualifies only if it contains `alerts.csv` or
`alerts.json`, so an outputs folder sitting beside the periods is not
mistaken for one.

**Each period is assessed independently and the results compared.**
Concatenating them would corrupt every per-period statistic: risk scores
normalise per 100 alerts, and two rules are z-scores against peers.

## Validation

Structural checks run before any analytics and report **every** issue,
not the first:

- required tables present and non-empty
- required columns present
- duplicate `alert_id` (ERROR)
- alerts referencing an unknown `soc_id` (ERROR)
- unrecognised severity values (WARNING)
- escalations referencing an unknown `alert_id` (WARNING)
- `coverage_percentage` outside 0–100 (WARNING)

**ERRORs block. WARNINGs do not**, by default — a submission with
warnings is usually still analysable. `strict_validation` in Settings
widens the gate to include warnings; it never narrows it.

Failure raises `DatasetValidationError`, which carries the full report so
the UI can show every issue rather than a bare message.

## Ground truth (synthetic datasets only)

`data/generator/generate_dataset.py` writes `ground_truth.json` beside
the tables, recording which conditions were deliberately injected into
which records. See [VALIDATION.md](VALIDATION.md).

It is JSON, **not** a CSV table, deliberately: the loader reads a fixed
list of table names from `*.csv`, so a `ground_truth.csv` could be
mistaken for submitted evidence — and a real CSE submission would then
differ structurally from a synthetic one. A test asserts it is invisible
to ingestion.

## Generating a dataset

```bash
# Single period
python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 800 \
    --out data/synthetic

# Multiple periods, for trend analysis
python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 400 \
    --periods 4 --out data/multi-period
```

Each SOC is seeded with one of five risk profiles — `clean`, `typical`,
`weak`, `low_activity`, `poor_recordkeeping` — so the dataset is
defensible rather than arbitrary, and every rule has something to find.
Sectors are distributed so every peer group has at least three members.
