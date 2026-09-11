# SAT-SA — Architecture

## The organising principle

**The deterministic analytics layer is authoritative. Everything else
presents what it decided.**

Every finding is produced by a rule whose thresholds are published in
`config/assessment_rules.yaml`, whose arithmetic is linear, and whose
evidence traces back to rows in the submission. No model sits between a
finding and its evidence. The optional language model restates findings
that already exist; it cannot create, remove, or re-score one, and the
application runs identically with it switched off.

This is not a stylistic preference. A supervisory finding may be put to
the entity it concerns, so it has to be defensible: an examiner must be
able to say *this rule, this threshold, these rows*.

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│  application/          PySide6 desktop application          │
│    ui.py, session.py, pages/, widgets/, services/           │
└───────────────────────────┬─────────────────────────────────┘
                            │  calls services; never analytics directly
┌───────────────────────────▼─────────────────────────────────┐
│  analytics/            the engine — runs headless           │
│    ingestion/  validator  normalizer  metrics/  detection/  │
│    scoring/  evidence  review_queue  benchmarking           │
│    capabilities  trends  validation  reporting  narration/  │
└─────────────────────────────────────────────────────────────┘
```

The dependency runs **one way**. `analytics/` never imports from
`application/`, and carries no Qt import. `requirements.txt` installs
the engine alone, with no GUI dependency, so the pipeline runs on a
machine that has no display. A test asserts this direction holds.

## The pipeline

```
  SOC submission
        │
        ▼
  ingestion/           CSV folder · ZIP · JSON · SQLite  →  one internal model
        │              archives inspected before a byte is written
        ▼
  validator.py         structural checks; reports every issue, not the first
        │              blocking errors stop here
        ▼
  normalizer.py        build_alerts_enriched — the single flat join
        │              every detector reads
        ▼
  metrics/             alert · analyst · case · escalation · telemetry
        │
        ▼
  detection/           9 execution-gap rules  +  5 negative-space rules
        │              every finding: evidence · rule_id · rationale
        ▼
  scoring/             score = Σ (weight × count per 100 alerts)
        │              linear, hand-recomputable
        ▼
  completeness.py      how much of the submission the checks could run
        │              against — reported BESIDE the score, never in it
        ▼
  benchmarking         position within the entity's own sector peer group
  capabilities         findings mapped to the eight supervisory areas
  trends               comparison across submission periods
        │
        ▼
  review_queue.py      findings on one alert correlated into one case,
        │              ranked by weight × severity boost; global top-N
        │              plus each entity's own worst case
        ▼
  evidence.py          any finding → the submitted rows behind it
        │
        ▼
  reporting.py         JSON · CSV · PDF
        │
        ▼
  narration/           OPTIONAL — restates findings in plainer prose
        │              never decides, never alters
        ▼
  HUMAN EXAMINER       forms the supervisory judgement
```

## Authoritative versus optional

The split that governs every other decision in the codebase:

| | Layer | May decide |
|---|---|---|
| **AUTHORITATIVE** | ingestion · validation · normalisation · detection · scoring · benchmarking · evidence · review queue | everything a supervisor acts on |
| **REPORTED ALONGSIDE** | evidence completeness | nothing — it describes the submission, not the entity |
| **OPTIONAL** | `narration/` — local LLM | nothing; it restates a decided finding as display text |

`analytics/` has no Qt import and no network import beyond the optional
loopback call in `narration/`. It runs headless, which is what makes
`main.py` and the test suite possible without a display.

## Three separated layers on every finding

```
evidence       raw structured facts — numbers, thresholds, IDs.
               Never phrased as judgement.
finding_type   the deterministic rule outcome. What the engine decided,
+ rule_id      objectively, from that evidence.
rationale      a human-readable INDICATOR. "may indicate", never
               "the analyst failed". Supervisory judgement stays with
               the reviewer.
```

Keeping these apart is what lets a language model narrate a finding
without ever being the thing that decided it.

## Desktop application

```
application/
  main.py            entry point (launched by desktop.py)
  ui.py              MainWindow — eight pages
  session.py         lifecycle state machine
  version.py         name and version
  pages/             settings_page.py
  widgets/           drop_zone · charts · finding_detail · app_icon
  services/          assessment · history · evidence · settings ·
                     narration · dashboard · review · rule_reference
```

### Lifecycle

```
NO_DATASET ──► VALIDATING ──► READY ──► ASSESSING ──► LOADED
                    │
                    └────────► INVALID   (blocking validation errors)
```

`SessionState` owns the phase and publishes derived properties —
`has_results`, `can_run_assessment`, `is_busy`. Page gating, the run
action and the status bar read those rather than each testing
attributes for itself.

**Narration is deliberately not a phase.** It runs after `LOADED` and
cannot move the session out of it: the assessment is complete and saved
before an explanation is ever requested, so a failed or cancelled
narration leaves a fully valid assessment behind.

### Threading

Validation, assessment and narration each run on a `QThread`. Qt object
references are **held, never nulled from inside a slot connected to
their own `finished` signal** — dropping the last Python reference to a
QThread mid-emission lets PySide6 collect the wrapper and segfaults in
`SignalManager::callPythonMetaMethod`. Busy state is tracked with
separate flags so nothing has to touch object lifetimes.

## Assessment history

Every completed assessment gets its own timestamped directory under
`assessments/`, written once and never reopened. `create_run` refuses to
return a path that already exists — two assessments started in the same
second get `-2`, `-3` — because a supervisory assessment is a record
that may be referred back to or produced as the basis for a finding.

Loading a past run reads **its own results file** rather than re-running
anything: it must show what it actually said, not what the current rule
set would say about the same data now.

## What does not exist, deliberately

| Not present | Why |
|---|---|
| Any network call | The tool is built for an air-gapped environment. The only URL anywhere in the codebase is `http://localhost`, for an optional local model. |
| A model in the decision path | Findings must be defensible to the entity they concern. |
| Recommended actions | There is no recommendation-generation logic, and narration restates findings rather than deciding what to do about them. A placeholder would misrepresent the tool. |
| Real-time monitoring | SAT-SA assesses periodic submissions. It is not a SOC, a SIEM, or a monitor. |
