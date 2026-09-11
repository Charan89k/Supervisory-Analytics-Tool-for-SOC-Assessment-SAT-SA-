# SAT-SA — Supervisory Analytics Tool for SOC Assessment

**Smart India Hackathon 2026 · Problem statement SIH26157 · NTRO / NCIIPC**

An offline desktop application that helps a supervisor assess how well a
Critical Sector Entity actually ran its Security Operations Centre —
using the entity's own alert and case-management records as evidence.

It supports supervisory judgement. **It does not replace it.**

---

## The problem

The **National Critical Information Infrastructure Protection Centre**
assesses the cyber resilience of **Critical Sector Entities** — banks,
power utilities, telecoms, hospitals. Each runs a **Security Operations
Centre**: analysts watching alerts, opening cases, escalating what
matters.

As part of an assessment, NCIIPC examiners read samples of those alert
and case records by hand. That manual review consistently finds things
no policy document, audit, self-assessment or KPI dashboard reveals — a
CRITICAL alert closed in three minutes, an escalation the entity's own
policy required that never happened, a telemetry source that has
reported nothing for months.

The purpose is not to assess individual alerts. The records are
**operational evidence** about whether the entity has working
capabilities: threat detection, investigation, escalation, incident
response, security operations, governance, operational discipline,
cyber resilience.

**Manual review works but does not scale.** An examiner can read a
sample of a few hundred records. A CSE produces hundreds of thousands,
and there are many CSEs.

## What SAT-SA does

It reads a submission and finds two things a policy review cannot.

**Execution gaps** — documented controls say one thing, the operational
evidence shows another:

> A CRITICAL alert was acknowledged at 02:14 and closed at 02:17 with no
> investigation ever started. Response-time metrics look excellent.

**Negative space** — evidence that *should* exist and does not:

> Only 26 of 205 HIGH/CRITICAL alerts carry an escalation record of any
> kind. Whether escalation happened cannot be determined either way.

Then it ranks entities by risk, prioritises what a human should read
first, and traces every finding back to the rows it came from.

```
  SOC submission
        │
        ▼
   Validation ──► Normalization ──► Metrics
        │
        ▼
   Detection      9 execution-gap rules + 5 negative-space rules
        │
        ▼
   Evidence  ·  Risk scoring  ·  Peer benchmarking  ·  Capability mapping
        │
        ▼
   Review prioritisation      ~1,500 findings → 25 cases worth reading
        │
        ▼
   HUMAN EXAMINER             forms the supervisory judgement
```

## What SAT-SA is not

| It is not | |
|---|---|
| a SIEM | it does not collect or correlate logs |
| a SOC | it does not detect intrusions |
| real-time monitoring | it assesses periodic submissions, after the fact |
| a centralised SOC | it holds no live connection to any entity |
| autonomous response | it takes no action, ever |
| a cloud or AI service | it runs entirely offline; see [AI.md](docs/AI.md) |
| a replacement for the examiner | it prioritises and evidences; a human decides |

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-desktop.txt

# Generate a synthetic dataset with seeded, reproducible ground truth
python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 800 \
    --out data/synthetic

# Launch SAT-SA
python desktop.py
```

`python desktop.py` **is the application.** There is no server to start
and no address to visit.

Inside it: **New Assessment** → drop or browse to `data/synthetic` →
validation runs automatically → **RUN ASSESSMENT** (~2 seconds) →
Dashboard, Findings, Review Queue, Benchmarking and Reports unlock.

### Demonstration mode

**New Assessment → Run Demonstration Assessment** (or `Ctrl+D`) builds a
synthetic submission and assesses it in about four seconds — nine
entities, all fourteen rules firing, three comparable peer groups.

It runs the **real pipeline**: the same ingestion, validation,
normalisation, detection, evidence, scoring and reporting a real
submission takes. Nothing is staged or precomputed. The only thing it
changes is where the dataset came from, and the data is labelled as
synthetic in the status bar, on the dashboard and in the dataset name.

### System dependencies

Linux needs Qt's platform libraries, which most desktops already have.
If the window fails to open:

```bash
sudo pacman -S libxcb xcb-util-wm xcb-util-image xcb-util-keysyms \
    xcb-util-renderutil libxkbcommon-x11          # Arch
sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libegl1   # Debian/Ubuntu
```

### Headless pipeline

The same analytics without a GUI:

```bash
python main.py --data data/synthetic --out outputs --export-csv --export-pdf
python main.py --data data/multi-period --out outputs --trends
python main.py --data data/synthetic --out outputs --validate
```

`requirements.txt` installs the engine alone — no Qt, no display needed.

## Accepted submissions

| Format | Notes |
|---|---|
| Folder of CSV tables | the primary format |
| Folder of JSON tables | |
| ZIP archive | inspected for unsafe paths and expansion before extraction |
| Single JSON file | `{"alerts": [...], "cases": [...]}` |
| SQLite export | opened read-only |

All five resolve to the same internal model — verified by test to
produce byte-identical assessments. A folder of period subdirectories is
assessed as multiple periods for trend analysis.

See [DATA_FORMAT.md](docs/DATA_FORMAT.md).

## Detection

Every threshold lives in `config/assessment_rules.yaml` and is tunable
without touching code.

**Execution gaps (9)** — `MISSED_ESCALATION` · `SLOW_TRIAGE` ·
`FAST_CLOSURE` · `MISSING_EVIDENCE` · `REOPENED_CASE` ·
`REPETITIVE_INVESTIGATION` · `ANALYST_OVERLOAD` ·
`ACK_WITHOUT_INVESTIGATION` · `REPEATED_ALERT_WITHOUT_REMEDIATION`

**Negative space (5)** — `TELEMETRY_GAP` · `MISSING_ALERT_CATEGORY` ·
`LOW_ACTIVITY_OUTLIER` · `MISSING_ESCALATION_RECORDS` ·
`MISSING_INVESTIGATIONS`

Every rule, its thresholds, its guards and its limitations:
[ANALYTICS.md](docs/ANALYTICS.md).

### Every finding carries three separate layers

```
evidence       raw structured facts. Never phrased as judgement.
rule_id        the deterministic rule outcome.
rationale      an INDICATOR — "may indicate", never "the analyst failed".
```

And traces the whole way down:

```
Finding → Rule → Rationale → Evidence → Source records
```

The last step re-reads the submission itself, so what an examiner
verifies is the entity's own data — not something the pipeline copied.

### Scoring is linear on purpose

```
supervisory_risk_score = Σ ( weight × (count / alerts) × 100 )
```

A supervisor can recompute any entity's score by hand. **No model sits
between the findings and the ranking.**

## The AI layer is optional and supplementary

```
Deterministic analytics → Finding + Evidence + Severity + Risk
                                    │  ◄── AUTHORITATIVE
                                    ▼
                        Optional local Qwen model
                                    │  ◄── SUPPLEMENTARY
                                    ▼
                          Human-readable explanation
```

The model **cannot** create, remove, or re-score a finding, change a
severity or queue position, or invent evidence. It receives a defensive
copy and can write nothing back; the UI keeps its output in a separate,
clearly labelled panel outside the authoritative record.

**AI is off by default.** With it disabled, unavailable, or crashed, the
assessment completes identically — every finding keeps its
rule-generated rationale. With it on, the default explains **10 of
~1,500 findings**, chosen by review-queue rank.

Backends: `llama.cpp` (shipped — a local `.gguf`, no service),
Ollama (development), and a labelled sample explainer for tests.
Details: [AI.md](docs/AI.md).

## Validation

```
ALL RULES   1,698 seeded   1,460 TP   4 FP   99.7% precision   86.0% recall
Ranking     100% of the top ten correspond to a seeded condition
Effort      81 prioritised items from 1,481 findings over 3,296 alerts
```

> **Synthetic validation demonstrates that the implemented rules behave
> according to their specification. It does not prove that the rules
> represent expert supervisory judgement.**

The generator injects the conditions the rules look for, so agreement
between them is partly circular. No expert validation has been
performed, and SAT-SA reports synthetic figures under that heading and
no other. [VALIDATION.md](docs/VALIDATION.md) explains scope-aware
recall, what a false positive means here, and the expert methodology.

## Offline by design

```
  No Internet · No cloud · No external AI API · No telemetry
```

A grep for any URL across the product code returns exactly one result —
`http://localhost`, for the optional local model. That check runs at the
end of every development phase.

[OFFLINE_DEPLOYMENT.md](docs/OFFLINE_DEPLOYMENT.md) covers air-gapped
transfer, model installation, hardware requirements and the model update
mechanism.

## Security

A submission is untrusted input. **Nothing in a dataset is ever
executed.** Archives are inspected before a byte is written — traversal
in six forms, symlink members, zip bombs, member counts, streaming size
limits. Real malicious archives are built and run in the test suite.
[SECURITY.md](docs/SECURITY.md).

## Testing

```bash
python -m pytest            # 396 tests, ~30s
python tests/smoke_ui.py    # 100 UI checks, ~21s
```

**No test loads a language model.** [TESTING.md](docs/TESTING.md).

## Performance

Measured on an 8th-generation Intel Core i5 U-series laptop:

| Dataset | Time | Findings |
|---|---|---|
| 3,296 alerts / 5 entities | 1.6s | 1,481 |
| 27,800 alerts / 12 entities | 9.2s | 11,810 |

## Project structure

```
desktop.py                  THE APPLICATION — launch this
main.py                     headless pipeline
app.py                      legacy Streamlit view — NOT the product

analytics/                  the engine; runs headless, no Qt
  ingestion/                CSV · JSON · ZIP · SQLite + safe extraction
  validator.py              structural checks
  normalizer.py             build_alerts_enriched — the shared join
  metrics/                  alert · analyst · case · escalation · telemetry
  detection/                execution_gaps.py · negative_space.py
  scoring/                  linear, hand-recomputable risk scores
  evidence.py               finding → the submitted rows behind it
  review_queue.py           case correlation and prioritisation
  benchmarking.py           peer comparison within a sector
  capabilities.py           findings → the eight supervisory areas
  trends.py                 comparison across submission periods
  validation.py             detection measured against ground truth
  reporting.py              JSON · CSV · PDF
  narration/                optional local model backends

application/                PySide6 desktop application
  ui.py                     MainWindow — eight pages
  session.py                lifecycle state machine
  pages/ widgets/ services/

config/assessment_rules.yaml   every threshold, weight and mapping
data/generator/                synthetic datasets with seeded ground truth
tests/                         396 tests + the UI smoke test
docs/                          the documents linked above
```

## Configuration

| What | Where |
|---|---|
| Rules, thresholds, weights, capability mapping | `config/assessment_rules.yaml` |
| Application settings | `~/.config/SAT-SA/settings.json` |
| Assessments | `assessments/<timestamp>/` — immutable |

| Environment variable | Effect |
|---|---|
| `SATSA_NARRATION_BACKEND` | force a backend; `mock` guarantees no model loads |
| `SATSA_ASSESSMENTS_DIR` | relocate assessment history |
| `SATSA_CONFIG_DIR` | relocate the settings file |

## Troubleshooting

**The window does not open.** Install the Qt platform libraries above.
Verify with `QT_QPA_PLATFORM=offscreen python tests/smoke_ui.py` — if
that passes, the application works and the problem is the display.

**"No ground_truth.json"** on `--validate`. Only synthetic datasets
carry labels. A real submission cannot be validated this way, which says
nothing about whether it contains findings.

**"does not contain multiple period subdirectories"** on `--trends`.
Generate with `--periods 4`.

**AI shows NOT CONFIGURED.** Expected without a model. Install
`llama-cpp-python`, point Settings at a `.gguf`, or select *Sample
explanations* to see the flow with no model.

**Peer benchmarking says the group is too small.** Fewer than three
entities in a sector. Regenerate with more, e.g. `--socs 12`.

**Tests fail after regenerating the dataset.** Expected if they assert
exact counts. Run `pytest` fresh; fixtures build their own assessment.

## Limitations

- **No expert validation has been performed.** Figures are synthetic.
- **`LlamaCppBackend.explain()` has not run against a real model.** It
  is structurally sound and configuration-tested; the generation path
  itself is unrun.
- **Source drill-down runs on the UI thread** — ~0.2s typically, ~1s at
  28,000 alerts, behind a wait cursor.
- **Live database connections and API ingestion are not implemented.**
  Named as planned; never offered by the UI.
- **Trends need multiple periods.** A single period reports no trend
  rather than drawing one through a point.
- **`app.py` is a legacy Streamlit view**, kept for development only. It
  is not the product and must not be used to evaluate SAT-SA.

## Roadmap

**Complete** — ingestion · validation · normalization · 14 detection
rules · evidence drill-down · risk scoring · peer benchmarking ·
capability mapping · trend analysis · review queue · assessment history ·
settings · reporting · local AI layer · validation framework ·
offline hardening · documentation · demonstration mode

**Remaining** — packaging (Windows `.exe`, Linux AppImage) · final QA

## Documentation

| Document | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | layers, pipeline, lifecycle, threading |
| [ANALYTICS.md](docs/ANALYTICS.md) | all 14 rules with real thresholds |
| [DATA_FORMAT.md](docs/DATA_FORMAT.md) | formats, internal model, validation |
| [AI.md](docs/AI.md) | the safety boundary and backends |
| [VALIDATION.md](docs/VALIDATION.md) | methodology and its limits |
| [SECURITY.md](docs/SECURITY.md) | untrusted-input handling |
| [OFFLINE_DEPLOYMENT.md](docs/OFFLINE_DEPLOYMENT.md) | air-gapped deployment |
| [TESTING.md](docs/TESTING.md) | how to run and what is defended |
| [CHANGELOG.md](CHANGELOG.md) | release history |
