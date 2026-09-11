# Changelog

All notable changes to SAT-SA. Versions follow
[Semantic Versioning](https://semver.org/).

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
