# SAT-SA — Validation Methodology

> **The honesty boundary, stated once and repeated in every report:**
> Synthetic validation demonstrates that the implemented rules behave
> according to their specification. It does **not** prove that the rules
> represent expert supervisory judgement.

## Two different things called "validation"

| | What it checks | Where |
|---|---|---|
| **Dataset validation** | Is this submission structurally sound? | every assessment |
| **Detection validation** | Do the rules find what is there? | labelled datasets only |

This document is about the second. For the first, see
[DATA_FORMAT.md](DATA_FORMAT.md).

## Ground truth

The generator knows which records it made faulty — it decides
`is_slow`, `is_fast`, `is_rubber_stamp`, `is_recurring_unremediated` as
it builds each alert. That knowledge is written to `ground_truth.json`
beside the dataset:

```json
{
  "alerts":   { "ALT-SOC-003-000229": ["ACK_WITHOUT_INVESTIGATION",
                                        "SLOW_TRIAGE"] },
  "entities": { "SOC-005": [{"condition": "MISSING_ESCALATION_RECORDS",
                              "detail": "escalation record rate 0.15"}] },
  "profiles": { "SOC-003": "weak" }
}
```

**A real submission carries no labels.** Their absence means *cannot
validate* — never *nothing was wrong*. The CLI says exactly that and
exits cleanly.

## Running it

```bash
python main.py --data data/synthetic --out outputs --validate
```

Writes `validation_report.txt` beside the assessment.

## What is measured

| Measure | Meaning |
|---|---|
| **True positive** | rule fired on a seeded condition |
| **False positive** | rule fired where nothing was seeded |
| **False negative** | seeded condition the rule did not fire on |
| **Precision** | TP / (TP + FP) |
| **Recall** | TP / (TP + FN) |
| **Scope-aware recall** | TP / conditions the rule can actually fire on |
| **Ranking quality** | share of the review queue, and of its top ten, matching a seeded condition |
| **Review effort** | share of the alert population a supervisor reads to reach the queue |

## Results on the reference dataset

```
ALL RULES   1,698 seeded   1,460 TP   4 FP   238 FN   99.7% precision   86.0% recall
Ranking     100% of the top ten correspond to a seeded condition
Effort      81 prioritised items from 1,481 findings over 3,296 alerts
```

Rules with no threshold that could legitimately suppress a seeded case —
`SLOW_TRIAGE`, `MISSED_ESCALATION`, `REOPENED_CASE`, `MISSING_EVIDENCE` —
detect **every** seeded case. A test asserts that, because any miss
there would be a real one.

## Scope-aware recall: why the naive number misleads

`ACK_WITHOUT_INVESTIGATION` reports **26.5%** recall measured against
every seeded condition. That figure is wrong in a specific way.

The rule is seeded across all severities but **scoped to HIGH/CRITICAL**,
because closing a LOW alert after triage without a formal investigation
record is normal SOC practice. Within its scope it finds **50 of 50**.

```
189 seeded  →  50 within HIGH/CRITICAL scope  →  50 fired  =  100% in scope
139 seeded outside the scope — declined deliberately, not missed
```

Publishing 26.5% would understate the rule and, worse, invite someone to
widen a scope that exists to prevent false positives.

**In-scope recall is computed only where the scope is a plain attribute
predicate** (severity, status) that can be derived from the alerts table
independently of the detector.

**Where the scope is a computed threshold** — `FAST_CLOSURE`'s 5-minute
margin, `REPETITIVE_INVESTIGATION`'s occurrence count — the threshold is
**named but not recomputed**. Re-deriving detector logic inside the
validator would let the two drift together and agree with each other
rather than with reality. Those rules report recall uncorrected, with the
reason printed beside it.

## What a false positive means here

**It means the rule fired on a record the generator did not seed. That is
not the same as the rule being wrong.**

Three of the four false positives on the reference dataset are one
analyst writing byte-identical investigation notes three times for the
same asset. That is a genuine instance of exactly what
`REPETITIVE_INVESTIGATION` detects — it arose incidentally, as it would
in a real submission.

Conditions occur by chance in generated data as in real data. These
figures are a **floor on precision**, not a defect count. Reporting them
without that context would invite tuning away correct behaviour.

## What is not measured

`ANALYST_OVERLOAD` is emitted at a grain the generator does not label,
so **no measurement is claimed**. The report names it under
NOT VALIDATED rather than omitting it silently.

## The limits of synthetic validation

**What it can establish**

- a rule fires on the conditions it was written to detect
- a rule does not fire where that condition was not injected
- the review queue puts genuinely-affected records near the top
- prioritisation reduces the reading a supervisor must do

**What it cannot establish**

- that the rules detect what an *expert examiner* would consider worth
  finding

The generator injects the conditions the rules look for, so agreement
between them is **partly circular**. These figures show the rules behave
as specified — not that the specification matches expert judgement.

## Expert validation — the methodology, not a claim

Closing that gap requires labelled records from a real manual review:

```
  Expert-reviewed sample of a real submission
            ↓
  Human-labelled findings  (what the examiner considered worth finding)
            ↓
  Run SAT-SA over the same records
            ↓
  Compare:  agreement · what SAT-SA found that the examiner did not
            · what the examiner found that SAT-SA did not
            · whether the queue order matches the examiner's priorities
            · review effort saved
```

**No expert validation has been performed.** SAT-SA reports synthetic
figures under that heading and no other. The framework accepts labels in
the same shape, so expert labels can be supplied when they exist — but
the tool will not describe synthetic results as expert results.
