# SAT-SA — Supervisory Analytics Tool for SOC Assessment

SAT-SA analyzes the operational records a SOC/CSE already produces —
alerts, cases, escalations, evidence, telemetry — and surfaces where
documented process didn't match what actually happened, and where
expected evidence is missing entirely. It does **not** do intrusion
detection or vulnerability scanning; it supervises the people and
process running those tools.

## Why this exists

Two independent failure modes matter for supervisory assessment:

- **Execution gaps** — a control or SLA exists, but the evidence
  shows it wasn't followed (a HIGH alert closed in 3 minutes, an
  escalation that was required but never initiated).
- **Negative space** — evidence that *should* exist but doesn't (a
  telemetry source with no data, a SOC with suspiciously low alert
  volume, cases with no investigation notes at all). There's no row
  to point to here — only an absence — so every negative-space rule
  has to say what was expected and why.

Every finding carries three deliberately separate layers: `evidence`
(raw numbers, no judgement), `finding_type`/`rule_id` (the
deterministic, auditable rule outcome), and `rationale` (a
human-readable *indicator*, phrased as "may suggest," never as an
accusation). Supervisory judgement stays with the human reviewer.

## Architecture

```
ingestion (CSV / JSON / ZIP / SQLite) -> validation -> normalization
    -> metrics (alerts, analysts, cases, escalations, telemetry)
    -> execution-gap detectors --\
    -> negative-space detectors --+-> finding counts -> risk scoring -> peer benchmarking
    -> supervisory review queue (ranks individual findings, not just entities)
    -> evidence drill-down (traces any finding back to source records)
    -> reporting (JSON / CSV / PDF)
    -> local explanation layer (optional - restates findings in plain
       language; never decides them)
    -> PySide6 desktop application
```

Scoring is deliberately linear and auditable:
`score = sum(weight_i * normalized_count_i)` - a supervisor can
recompute any entity's score by hand from the finding counts in
`config/assessment_rules.yaml`. No model sits between the findings
and the final ranking.

## Detection rules

**Execution gaps** (9): `MISSED_ESCALATION`, `SLOW_TRIAGE`,
`FAST_CLOSURE`, `MISSING_EVIDENCE`, `REOPENED_CASE`,
`REPETITIVE_INVESTIGATION`, `ANALYST_OVERLOAD`,
`ACK_WITHOUT_INVESTIGATION`, `REPEATED_ALERT_WITHOUT_REMEDIATION`

**Negative space** (5): `TELEMETRY_GAP`, `MISSING_ALERT_CATEGORY`,
`LOW_ACTIVITY_OUTLIER`, `MISSING_ESCALATION_RECORDS`,
`MISSING_INVESTIGATIONS`

All thresholds live in `config/assessment_rules.yaml` - tunable
without touching code, with every number commented.

## The application

**SAT-SA is a standalone offline desktop application.** It is not a web
application, not a browser dashboard, and not a service. There is no
server to start and no address to visit.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-desktop.txt

# Generate a synthetic dataset with seeded, reproducible ground truth
python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 800 --out data/synthetic

# Launch SAT-SA
python desktop.py
```

The workflow inside the application:

```
Launch -> Dashboard -> New Assessment -> select or drop a submission
       -> validation result -> Run Assessment
       -> Findings / Review Queue / Reports
```

Accepted submissions: a folder of CSV or JSON tables, a ZIP archive, a
single JSON file, or a SQLite export. Archives are inspected for unsafe
member paths and expansion limits before anything is written to disk.

### Headless pipeline

The same analytics run without a GUI, for scripted or batch assessment:

```bash
python main.py --data data/synthetic --out outputs --export-csv --export-pdf
```

`main.py` flags:
- `--export-csv` - writes `entity_risk_scores.csv`, `execution_gap_findings.csv`,
  `negative_space_findings.csv`, `review_queue.csv` alongside the JSON output
- `--export-pdf` - writes a one-page-per-entity `executive_summary.pdf`
- `--narrate` - runs the top of the review queue through the local
  explanation layer (see below). Falls back to the rule-generated
  rationale whenever no local model is available, so the flag is always
  safe to pass.

### `app.py` is not the application

`app.py` is a **legacy Streamlit dashboard**, kept only as a development
and reference view of an assessment that has already been produced. It
is not the product, it is not part of the deliverable, and it should not
be used to evaluate SAT-SA: it runs a local web server, which the
deployment requirements explicitly exclude. The desktop application
(`desktop.py`) is the only supported interface.

## Local explanation layer — optional by design

An optional local language model can restate findings in plainer prose
for a reviewer. It is architecturally incapable of doing more than that:

- **Never load-bearing.** Every finding already carries a deterministic,
  rule-generated `rationale` before narration runs. Turn the layer off
  entirely and the assessment output is identical in substance.
- **Cannot alter a finding.** The narration service writes only
  `narration_*` fields, and a backend is handed a defensive copy of the
  finding rather than the authoritative record — so a backend that
  mutates what it is given changes nothing. It cannot create, remove, or
  re-score a finding, change a severity, or invent evidence.
- **Fails without failing the assessment.** If no model is available the
  application reports "AI unavailable" and every finding keeps its rule
  rationale. The assessment still completes.
- **Bounded by default.** An assessment produces ~1,500 findings; the
  default sends **10** to the model, chosen by review-queue rank. Severity
  is not the scope control — the queue is already almost entirely
  Critical/High, so filtering on it bounds nothing.

### Backends

```
LocalLLMBackend
├── LlamaCppBackend   shipped: a local .gguf file, no service to install
├── OllamaBackend     development convenience on a workstation
└── MockBackend       tests and demo: deterministic samples, no model
```

`LlamaCppBackend` is the deployment path. The model is a **data file**
that travels on the same media as the dataset, not installed software —
an easier story for an air-gapped environment than a background service
on a TCP port. Updating the model means replacing the file.

`MockBackend` produces deterministic sample text so the whole narration
pipeline can be developed and tested without executing a multi-gigabyte
model. Its output is labelled `[SAMPLE EXPLANATION]`, carries
`is_mock=True`, and states that no language model was involved — it is
never presentable as model analysis.

**Offline means local compute, not local speed.** A 7B model on a
CPU-only laptop takes roughly 1-4 minutes per explanation. The AI layer
is off by default, and the default model is `qwen2.5:7b` — never the
14B, which needs ~9 GB resident.

## Synthetic data & ground truth

`data/generator/generate_dataset.py` seeds each SOC with one of five
risk profiles so the dataset is defensible rather than arbitrary:

- `clean` - low gap rates across the board
- `typical` - moderate, realistic rates
- `weak` - elevated execution-gap rates
- `low_activity` - deliberately sparse alert volume, to exercise
  `LOW_ACTIVITY_OUTLIER`
- `poor_recordkeeping` - alerts/cases exist but escalation and
  investigation records are largely absent, to exercise the two
  record-keeping negative-space rules

## Testing

```bash
pytest                                    # full suite
python tests/smoke_ui.py                  # headless UI smoke test
```

Every detection rule has both a positive case (fires) and a negative
case (does not fire) - a rule that's only ever tested on cases where
it should fire says nothing about its false-positive rate.

Neither the suite nor the smoke test loads a language model. Roughly
half the ingestion tests are adversarial, building genuinely malicious
archives (path traversal, symlink members, zip bombs) and asserting
nothing is written outside the extraction root.

## Project layout

```
analytics/
  ingestion/                                # CSV/JSON/ZIP/SQLite adapters + safe extraction
  loader.py, validator.py, normalizer.py    # ingestion pipeline
  narration/                                # local explanation backends
  metrics/                                  # alert/analyst/case/escalation/telemetry metrics
  detection/                                # execution_gaps.py, negative_space.py
  scoring/                                  # score.py, benchmark.py
  evidence.py                               # drill-down: alert_id/case_id -> full record bundle
  review_queue.py                           # ranks individual findings for human review
  llm_narration.py                          # offline Qwen explanation layer
  reporting.py                              # CSV + PDF exports
schemas/assessment_result.py                # output contract (dataclasses)
data/generator/generate_dataset.py          # synthetic dataset with seeded ground truth
config/assessment_rules.yaml                # every threshold, weight, and setting
desktop.py                                  # THE APPLICATION — launch this
application/                                # PySide6 desktop app
  ui.py, session.py, version.py             #   window, lifecycle state, identity
  pages/, widgets/, services/               #   settings page, drop zone, services
main.py                                     # headless pipeline entrypoint
app.py                                      # legacy Streamlit view — NOT the product
tests/                                      # detectors, ingestion, narration,
                                            #   pipeline, and a headless UI smoke test
```
