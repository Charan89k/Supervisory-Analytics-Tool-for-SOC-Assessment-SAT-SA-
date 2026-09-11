# SAT-SA — Testing

```bash
python -m pytest                    # full suite
python tests/smoke_ui.py            # headless UI smoke test
```

**No test loads a language model.** Both runners force
`SATSA_NARRATION_BACKEND=mock`, so an automated run can never spin up a
multi-gigabyte model or depend on what is installed on the machine.

## Current state

| | |
|---|---|
| Test suite | **541 tests**, ~50s |
| UI smoke test | **112 checks**, ~25s |
| Model processes spawned | **0** |

| File | Tests | Covers |
|---|---:|---|
| `test_detectors.py` | 48 | every rule: fires **and** does not fire |
| `test_ingestion.py` | 43 | formats + adversarial archives |
| `test_narration.py` | 30 | backends, scope, the never-alters invariant |
| `test_dashboard.py` | 30 | dashboard computation, peer caveats |
| `test_ai_integration.py` | 26 | disabled / unavailable / mock / cancellation |
| `test_review_queue.py` | 24 | case correlation and prioritisation |
| `test_ollama_runtime.py` | 24 | runtime discovery, all four AI states |
| `test_validation.py` | 22 | ground truth, scope-aware recall, honesty |
| `test_session.py` | 22 | lifecycle, narration boundary, settings |
| `test_reporting.py` | 22 | PDF sections, CSV exports, layering |
| `test_history.py` | 22 | immutability of stored assessments |
| `test_demo.py` | 21 | demonstration mode uses the real pipeline |
| `test_validation_exposure.py` | 20 | validation in the GUI, and its boundary |
| `test_capabilities.py` | 20 | rule → supervisory area mapping |
| `test_trends.py` | 19 | trend arithmetic; no fabricated direction |
| `test_pipeline.py` | 19 | end-to-end assessment |
| `test_traceability.py` | 17 | finding → rule → evidence → source rows |
| `test_completeness.py` | 17 | evidence completeness, and that it scores nothing |
| `test_benchmarking.py` | 17 | peer groups, medians, small-group caveats |
| `test_app_settings.py` | 16 | settings persistence |
| `test_queue_coverage.py` | 13 | no CSE starved out of the review queue |
| `test_low_activity_baseline.py` | 13 | sector-relative volume outliers |
| `test_report_discovery.py` | 12 | single- and multi-period report artifacts |
| `test_packaging.py` | 10 | frozen-build paths, self-check |
| `test_missing_category_baseline.py` | 10 | sector-relative category coverage |
| `test_trend_ground_truth.py` | 4 | seeded trajectories hold across periods |
| **Total** | **541** | |

Plus `tests/smoke_ui.py` — **112 checks** driving the real `MainWindow`
offscreen, from dataset load through assessment, findings, evidence
drill-down, review queue, reports, history and settings.

## By category

| Category | Where |
|---|---|
| Unit — detectors, scoring, trends, capabilities | `test_detectors.py`, `test_trends.py`, `test_capabilities.py` |
| Integration — full pipeline, traceability | `test_pipeline.py`, `test_traceability.py` |
| Ingestion security — hostile archives | `test_ingestion.py` |
| AI boundary — the LLM alters nothing | `test_narration.py`, `test_ai_integration.py`, `test_ollama_runtime.py` |
| Peer-relative analytics | `test_low_activity_baseline.py`, `test_missing_category_baseline.py`, `test_benchmarking.py` |
| Supervisory coverage | `test_queue_coverage.py` |
| Evidence completeness | `test_completeness.py` |
| Validation framework | `test_validation.py`, `test_validation_exposure.py` |
| Packaging and frozen paths | `test_packaging.py` |
| UI behaviour | `tests/smoke_ui.py` |

## What the tests are actually defending

Most are not coverage for its own sake. They pin decisions that would
otherwise be easy to undo by accident.

**Every rule has a negative case.** A detector only ever tested on cases
where it should fire says nothing about its false-positive rate.

**Adversarial ingestion.** Genuinely malicious archives are built and
run — traversal in six forms, symlink members, zip bombs, member-count
and size limits — and the tests assert nothing is written outside the
extraction root.

**The AI boundary.** A deliberately hostile backend that rewrites
severity, priority, rule_id and nested evidence on everything handed to
it is asserted to change nothing.

**No CSE is starved.** A few high-volume entities filling the global
queue must not push every other entity out of the supervisor's worklist,
and an entity with nothing to review must not be padded into it.

**Peer baselines stay peer-relative.** Both sector-sensitive rules are
tested against the pooled comparison they replaced, including the case
where pooling masks a genuine blind spot.

**Evidence completeness scores nothing.** The assessment must be
identical with and without it; no finding may carry a completeness
field, and it may never appear as a weighted component.

**History immutability.** Four runs created in a tight loop must get
four directories; an earlier run must survive a later one.

**Settings actually work.** Each behavioural setting is tested by
exercising the behaviour, not the widget — export flags change which
files appear, the member limit actually rejects an archive, strictness
actually blocks on a warning.

**Documentation cannot drift.** Tests assert every emitted rule has a
scoring weight, a capability mapping, and a rule description whose
configured thresholds still resolve.

**Layering.** `analytics/` must not import from `application/` or Qt, or
a core-only install breaks.

## UI smoke test

Drives the real `MainWindow` under Qt's offscreen plugin through the
whole workflow: launch → gating → refusing an unsupported input →
validation pass and fail → assessment → dashboard → findings → source
drill-down → review queue → capability mapping → benchmarking →
multi-period trends → history → settings → reports → window state.

Two things make it runnable anywhere:

- `SATSA_NARRATION_BACKEND=mock` — no model is loaded
- `MainWindow(interactive=False)` — modal dialogs are recorded rather
  than shown. A `QMessageBox` under the offscreen plugin blocks the
  event loop forever; this is what previously turned the smoke test into
  a hang.

`SATSA_CONFIG_DIR` and `SATSA_ASSESSMENTS_DIR` point at temporary
directories, so a smoke run never touches real settings or history.

## Detection validation

Separate from the test suite — it measures how well the rules find what
is there, rather than whether the code works:

```bash
python main.py --data data/synthetic --out outputs --validate
```

See [VALIDATION.md](VALIDATION.md).

## Performance

Measured on an 8th-generation Intel Core i5 U-series laptop:

| Dataset | Time | Findings | Peak memory |
|---|---|---|---|
| 3,296 alerts / 5 entities | 1.5s | 1,481 | — |
| 4,942 alerts / 15 entities (demonstration) | 2.9s | 2,860 | — |
| 58,240 alerts / 30 entities | 21.0s | 29,836 | 373 MB |

A local benchmark on the development machine, not a capacity guarantee.

## Notes for contributors

- Tests must not read `outputs/` — it is a mutable work area the
  application rewrites. Use the session-scoped `assessment` fixture,
  which produces its own assessment in a temporary directory.
- Avoid asserting exact finding counts. The generator changes; assert
  relationships instead.
- After any UI change, run the smoke test as well as the suite.
