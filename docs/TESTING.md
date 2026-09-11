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
| Test suite | **396 tests**, ~30s |
| UI smoke test | **100 checks**, ~21s |
| Model processes spawned | **0** |

| File | Tests | Covers |
|---|---:|---|
| `test_detectors.py` | 48 | every rule: fires **and** does not fire |
| `test_ingestion.py` | 43 | formats + adversarial archives |
| `test_dashboard.py` | 30 | dashboard computation, peer caveats |
| `test_narration.py` | 29 | backends, scope, the never-alters invariant |
| `test_ai_integration.py` | 26 | disabled / unavailable / mock / cancellation |
| `test_review_queue.py` | 24 | case correlation and prioritisation |
| `test_validation.py` | 22 | ground truth, scope-aware recall, honesty |
| `test_session.py` | 22 | lifecycle, narration boundary, settings |
| `test_reporting.py` | 22 | PDF sections, CSV exports, layering |
| `test_history.py` | 22 | immutability of stored assessments |
| `test_capabilities.py` | 20 | mapping completeness, no double counting |
| `test_trends.py` | 19 | trend direction, never fabricated |
| `test_pipeline.py` | 19 | end to end, multi-period |
| `test_traceability.py` | 17 | finding → rule → evidence → source |
| `test_benchmarking.py` | 17 | peer comparison honesty |
| `test_app_settings.py` | 16 | every setting changes behaviour |

Roughly 5,900 lines of test code against 12,500 lines of product code.

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

| Dataset | Time | Findings |
|---|---|---|
| 3,296 alerts / 5 entities | 1.6s | 1,481 |
| 27,800 alerts / 12 entities | 9.2s | 11,810 |

## Notes for contributors

- Tests must not read `outputs/` — it is a mutable work area the
  application rewrites. Use the session-scoped `assessment` fixture,
  which produces its own assessment in a temporary directory.
- Avoid asserting exact finding counts. The generator changes; assert
  relationships instead.
- After any UI change, run the smoke test as well as the suite.
