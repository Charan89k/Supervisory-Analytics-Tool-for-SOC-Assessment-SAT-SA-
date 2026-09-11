# Contributing to SAT-SA

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 800 \
    --out data/synthetic
python -m pytest && python tests/smoke_ui.py
```

The synthetic dataset is **not** in version control — generate it before
running the suite. Tests that need it skip rather than fail.

## The rules this project is built on

**1. The deterministic layer is authoritative.** A finding must be
defensible to the entity it concerns. No model may create, remove or
re-score one. If a change puts a model anywhere in the decision path,
it is the wrong change.

**2. Never claim more than the tool establishes.** If an area has no
rule, say "not assessed" rather than showing it clean. If a peer group
has two members, withhold the percentile. If validation is synthetic,
call it synthetic. This is the single most important convention here.

**3. A setting that does nothing is worse than no setting.** Wire it or
do not expose it.

**4. Analytics must not import from the application layer.** The engine
runs headless; `requirements.txt` carries no Qt.

## Adding a detection rule

1. Implement it in `analytics/detection/`, returning the standard
   columns with an `evidence` dict and a **hedged** rationale.
2. Register it in `run_all_*_detectors`.
3. Add a weight in `config/assessment_rules.yaml` — the key must equal
   `finding_type.lower()`. A test enforces this; a mismatch previously
   made a rule score 0.0 silently.
4. Add a capability mapping under `capability_mapping.rules`, with a
   `why`.
5. Add a description to `application/services/rule_reference.py`.
6. Seed ground truth in the generator, or the rule cannot be validated.
7. Write **both** a fires and a does-not-fire test.

Steps 3–5 are enforced by tests, so a rule cannot ship half-registered.

## Testing conventions

- Every rule needs a negative case.
- Do not read `outputs/` — use the `assessment` fixture.
- Do not assert exact finding counts; assert relationships.
- Run the UI smoke test after any UI change.
- No test may load a language model.

## Commits

Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`.

Explain **why**, not just what. The commit history is part of the
project's documentation — several commits record a defect that was found
and the reasoning behind the fix, which is more useful than a summary of
the diff.
