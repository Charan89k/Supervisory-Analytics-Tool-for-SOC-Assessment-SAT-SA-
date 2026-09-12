# Changelog

All notable changes to SAT-SA. Versions follow
[Semantic Versioning](https://semver.org/).

## [0.9.3] — 2026-09-12

The first version published as a downloadable Windows build. No change
to the deterministic engine, the human-in-the-loop boundary, the AI
constraints, evidence provenance or the offline architecture.

### Local AI explanations

- **A zero explained count now says why.** "AI Explained 0 / 1,481" has
  three quite different causes — the feature is off, it is on but
  nobody has started it, or it ran and failed — and the number alone
  distinguished none of them. On a machine whose Settings page reported
  the model as working, this was indistinguishable from a broken AI.
  The dashboard tile now names the case and where the control lives;
  the Explain button is on the Review Queue, not the dashboard, so the
  caption names both.
- **The disabled reason is on screen, not in a tooltip.** It could
  previously only be found by hovering something that looks broken.
- **Timeouts no longer fail silently.** The Ollama backend caught every
  exception and returned nothing, so a model too slow for the timeout
  produced "0 explained; 1 failed" with no cause. Measured on a
  CPU-only machine, `qwen2.5:14b` exceeds the 180s timeout while
  `qwen2.5:3b` answers in 108s. The reason and the way out now reach
  the supervisor. A failed explanation still keeps its rule rationale.

### Packaging

- **Windows CI could never have passed.** The workflow installed the
  runtime dependencies and PyInstaller, then ran `python -m pytest`;
  pytest is deliberately not a runtime dependency, so the gate failed
  with "No module named pytest" before running a single test.
- **The release workflow took an input that could not work.** Its
  `attach_to_release` dispatch input had no tag to attach to, because
  the action derives the tag from the pushed ref. Removed: tagging is
  what publishes, and a manual run stops at the run artifact.
- **Package size was stated two ways.** ~180 MB in two places, 320 MB
  in two others. Measured: 179 MB unpacked, 76 MB zipped.

### Documentation

- `docs/TESTING.md` listed per-file counts totalling 541 against a
  suite of 611, missing two files entirely. Regenerated from an actual
  collection; the suite is 626.

## [0.9.1] — 2026-09-11

Packaging, zero-configuration AI, and seven defects found by auditing
the product against the SIH26157 problem statement. No change to the
deterministic authority model, the human-in-the-loop boundary, the AI
constraints, evidence provenance, ingestion security or the offline
architecture.

### Supervisory analytics

- **Review queue covers every CSE.** A global top-25 let a few
  high-volume entities own every top case: on a 30-entity submission
  21 of 30 received no queue item at all, one of them ranked 3rd by
  risk score with 202 CRITICAL findings. Selection is now the global
  top-N plus each entity's own worst case. Ordering, priorities and
  ranks are unchanged; an entity with no findings is not forced in.
- **Peer baselines are peer-relative.** `LOW_ACTIVITY_OUTLIER` and
  `MISSING_ALERT_CATEGORY` compared each entity against the whole
  submission while the configuration described a sector comparison.
  Pooling sectors masked genuine blind spots — a healthcare entity at
  150 alerts against peers averaging 900 scored -0.87 pooled and -2.26
  within its sector.
- **Small peer groups are no longer reported as assessed.** A sample
  z-score is bounded at (n-1)/sqrt(n), so a 3-entity group can never
  reach a -1.5 threshold. Such groups now state that rather than
  silently returning nothing, which reads as "no blind spots found".
- **Evidence completeness** (`analytics/completeness.py`). Records that
  were never written produce no findings, so poor record-keeping scored
  *better* than good record-keeping — 50.6 against 56.4 for an entity
  worse on every seeded rate. Rather than reweighting the model, SAT-SA
  now publishes how much of each submission the checks could actually
  run against, beside the risk score and never inside it.
- `LOW_ACTIVITY_OUTLIER` now carries structured evidence; it was the
  only rule publishing none.
- Case ordering is deterministic under ties.

### Desktop application

- **Multi-period reports are reachable.** Per-period artifacts live
  under `periods/<name>/` and the Reports page scanned only the run
  root, hiding four of eight artifacts behind manual browsing.
- **Validation evidence in the GUI.** Precision, recall, TP/FP/FN,
  scope-aware recall, ranking quality and review-effort reduction were
  CLI-only. Shown under a caveat stating they measure the rules against
  seeded labels, not any entity, and are not expert review.
- **Explanations are requested, not automatic** — `[ Explain Top
  Findings ]` on the Review Queue.
- Assessment history no longer resolves its location from `__file__`,
  which in a packaged build is the temporary unpack directory.

### AI layer

- **Zero-configuration discovery.** Ollama is the default; the runtime
  and model are found automatically, with no paths, endpoint or GGUF
  location asked of the user. `LlamaCppBackend` is unchanged and
  remains supported where a resident service is not permitted.
- Probe distinguishes NOT INSTALLED / NOT RUNNING / MODEL MISSING /
  READY, each naming the one action that resolves it.

### Packaging

- PyInstaller spec (onedir), offline-first `SAT-SA-Setup-AI.ps1` with
  checksum verification, and `SAT-SA --self-check`.

### Validation and test data

- **Trend ground truth was not ground truth.** The generator reseeded
  per period, so entities beyond the first five had their profile
  re-drawn each period and no trajectory at all. Seeded-direction
  agreement went from 5/9 to 8/8; the trend algorithm was correct
  throughout.
- Demonstration dataset raised to 15 entities in 3 peer groups of 5,
  so the sector-relative rules are exercised by construction rather
  than by coincidence. All 14 rules fire.
- Rule-coverage gates compute what a dataset can exercise instead of
  hardcoding a count, so they re-arm themselves.

### Performance

- Evidence completeness: 17.25s to 0.60s on 58,240 alerts.

### Verified

541 tests, 112 UI smoke checks. 58,240 alerts / 30 CSEs in 20.99s and
373 MB. Findings and entity ranking unchanged from the pre-fix
baseline. Validation 99.7% precision / 86.0% recall. One URL in product
code: `http://localhost`.

## [0.9.0] — 2026-09-11

Documented engineering baseline. Feature-complete for supervisory
assessment; demo mode and packaging remain.

### Assessment engine

- **14 detection rules** — 9 execution-gap, 5 negative-space. Every
  threshold in `config/assessment_rules.yaml`.
- **Ingestion adapters** — CSV folder, JSON file or folder, ZIP archive,
  SQLite export. All resolve to one internal model, verified to produce
  byte-identical assessments.
- **Source-record traceability** — any finding drills down to the
  submitted rows behind it, at whatever grain the rule fired on: alert,
  case, analyst, asset or telemetry source.
- **Peer benchmarking** within sector groups, on rates normalised per
  100 alerts, withheld where the group is too small to support a
  comparison.
- **Capability mapping** — all 14 rules to the eight supervisory areas
  the problem statement names, config-driven with a stated reason per
  assignment.
- **Trend analysis** across submission periods, each assessed
  independently. Direction requires consistent movement, not a net
  difference between two noisy endpoints.
- **Validation framework** measuring detection against seeded ground
  truth, with scope-aware recall and an explicit synthetic/expert
  boundary.

### Desktop application

- Eight pages: Dashboard, New Assessment, Findings, Review Queue,
  Reports, Benchmarking, History, Settings.
- **Assessment history** — every run in its own immutable timestamped
  directory. Previously a second assessment overwrote the first.
- **Review queue grouped into cases** — findings sharing an alert are
  correlated, with the prioritisation arithmetic shown in full.
- **Settings** — General, AI, Explanation Scope, Data, Reports, Advanced,
  System. Every control wired to behaviour.
- Lifecycle state machine gating the results pages, with purposeful
  empty states.

### Local AI

- Backend abstraction: `llama.cpp` (shipped), Ollama (development),
  labelled sample explainer (tests and demo).
- Off by default. Disabled, unavailable or crashed, the assessment
  completes identically.
- Backends receive a defensive copy and can write nothing back; the UI
  keeps explanations in a separate panel outside the authoritative
  record.
- Scope bounded by queue rank and an explicit maximum — 10 of ~1,500
  findings by default.

### Security

- Archive extraction refuses traversal in six forms, symlink members,
  zip bombs, oversized and over-numerous members, with streaming byte
  counts because a ZIP header's declared size can lie.
- SQLite opened read-only; SQL table names from an internal allow-list.
- Nothing in a dataset is ever executed.

### Reporting

- PDF gains dataset validation, peer benchmarking, trends and
  per-entity capability standing. Sections appear only when the data
  supports them.
- Reports page lists every artifact the run produced, including ones it
  does not recognise.

### Testing

- 396 tests and a 100-check headless UI smoke test. No test loads a
  language model.

### Known limitations

- No expert validation has been performed; figures are synthetic.
- `LlamaCppBackend.explain()` has not run against a real model.
- Source drill-down runs on the UI thread behind a wait cursor.
- Live database and API ingestion are not implemented.

---

## Earlier development

Phases 1.5 through 11 built the product incrementally: defect closure,
ingestion architecture, the AI layer, the desktop shell, dashboard,
findings explorer, review queue, trends, benchmarking, capability
mapping and the validation framework. See the commit history.
