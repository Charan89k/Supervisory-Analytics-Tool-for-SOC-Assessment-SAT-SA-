# SAT-SA — Detection Rules

Every threshold below is read from `config/assessment_rules.yaml` and is
reproduced here as configured. Change a value there and the rule changes;
nothing is hard-coded in the detectors.

## How a finding is produced

```
  input records  →  metric  →  rule  →  finding  →  evidence
                                           │
                                           ├──► severity (from the alert)
                                           ├──► risk contribution
                                           │      weight × count per 100 alerts
                                           └──► review priority
                                                  weight × severity boost
```

Scoring is deliberately linear:

```
supervisory_risk_score = Σ ( weight_i × (count_i / alerts) × 100 )
```

A supervisor can recompute any entity's score by hand from the finding
counts and the weights. No model sits between the findings and the
ranking.

## Two categories

**Execution gaps** — documented controls say one thing, the operational
evidence shows another. There is a record to point at.

**Negative space** — evidence that *should* exist and does not. There is
no row to point at, only an absence, so every negative-space rule states
what was expected and why.

## The fourteen rules

### Execution gaps (9)

---

#### `MISSED_ESCALATION` — `ESC-REQUIRED-001`
- **Capability area:** Escalation · **Weight:** 3.0
- **Reads:** `escalations.required`, `escalations.initiated`
- **Fires when:** an alert's escalation record says escalation was
  required and also that none was initiated.
- **Configured:** `escalation.required_severities = ['HIGH', 'CRITICAL']`
- **Evidence:** severity, `escalation_required`, `escalation_initiated`
- **Limitation:** depends on the entity keeping escalation records. Where
  records are absent this rule cannot fire — that absence is
  `MISSING_ESCALATION_RECORDS` instead.

---

#### `SLOW_TRIAGE` — `ACK-SLA-001`
- **Capability area:** Security Operations · **Weight:** 1.0
- **Reads:** `alert_events` CREATED → ACKNOWLEDGED
- **Fires when:** acknowledgement delay exceeds a multiple of the
  entity's own acknowledgement SLA for that severity.
- **Configured:** `sla_minutes.ack = {LOW: 60, MEDIUM: 30, HIGH: 15,
  CRITICAL: 10}` · `slow_triage_sla_multiplier = 2.5`
- **Evidence:** actual delay, SLA target, multiplier, computed threshold
- **Limitation:** an operational and staffing signal before it is a
  performance one. Queue depth at the time is not available to the rule.

---

#### `FAST_CLOSURE` — `TRIAGE-FAST-001`
- **Capability area:** Investigation · **Weight:** 2.0
- **Reads:** `alert_events` INVESTIGATION_STARTED → CLOSED
- **Fires when:** a HIGH/CRITICAL alert's investigation is shorter than
  a fraction of the triage SLA, subject to a floor, **and** is below
  that threshold by at least a margin.
- **Configured:** `sla_minutes.triage = {LOW: 480, MEDIUM: 240, HIGH: 90,
  CRITICAL: 45}` · `fast_closure_sla_fraction = 0.15` ·
  `fast_closure_floor_minutes = 10` · `fast_closure_min_margin_minutes = 5`
- **Evidence:** duration, expected minimum, deviation, margin required
- **Guard:** the 5-minute margin exists so a 13.0-minute closure against
  a 13.5-minute threshold is not reported as though it were a
  3-minute one.

---

#### `MISSING_EVIDENCE` — `EVIDENCE-REQUIRED-001`
- **Capability area:** Investigation · **Weight:** 1.5
- **Reads:** `evidence` joined to `alerts`
- **Fires when:** a HIGH/CRITICAL alert was closed with no evidence
  record attached.
- **Evidence:** severity, `evidence_record_count: 0`
- **Limitation:** indicates absence of a *record*, not that no
  investigation occurred. The rationale says so.

---

#### `REOPENED_CASE` — `REOPEN-001`
- **Capability area:** Incident Response · **Weight:** 1.0
- **Reads:** `alert_events` REOPENED
- **Fires when:** a REOPENED event exists in the alert's lifecycle.
- **Limitation:** reopening can reflect legitimate new information. It is
  an indicator, not proof of an incomplete first pass.

---

#### `REPETITIVE_INVESTIGATION` — `TEMPLATE-INVESTIGATION-001`
- **Capability area:** Operational Discipline · **Weight:** 2.0
- **Reads:** `cases.investigation_notes`
- **Fires when:** byte-identical note text repeats across cases for one
  analyst.
- **Configured:** `repetitive_investigation_min_occurrences = 3`
- **Why exact match, not fuzzy similarity:** two genuine notes sharing a
  phrasing template but differing in an embedded asset ID still score
  above 0.9 similarity, because the shared boilerplate dominates the
  ratio. Genuine notes almost never match byte-for-byte; exact
  duplication is the signal worth flagging.
- **Limitation:** reads `investigation_notes` (free text), never
  `resolution_reason` (a coarse field with a few dozen values
  dataset-wide).

---

#### `ANALYST_OVERLOAD` — `WORKLOAD-ZSCORE-001`
- **Capability area:** Security Operations · **Weight:** 1.5
- **Reads:** alert counts per analyst within a SOC
- **Fires when:** an analyst's alert volume is a z-score outlier against
  peers **in the same SOC**.
- **Configured:** `analyst_overload_zscore_threshold = 2.0`
- **Grain:** one finding per analyst, not per alert.
- **Limitation:** a resourcing and workload-distribution signal, not a
  judgement on the analyst.

---

#### `ACK_WITHOUT_INVESTIGATION` — `INVESTIGATION-ABSENT-001`
- **Capability area:** Operational Discipline · **Weight:** 2.5
- **Reads:** `alert_events` ACKNOWLEDGED, INVESTIGATION_STARTED, CLOSED
- **Fires when:** an alert was acknowledged and later **closed** with no
  INVESTIGATION_STARTED event ever recorded.
- **Configured:** `ack_without_investigation_severities = ['HIGH', 'CRITICAL']`
- **Why closure is required:** without it the rule matches every alert
  picked up but not yet worked — analysts doing their job at the moment
  the submission was cut. On the reference dataset that was 480 alerts,
  all open or in progress: a 100% false-positive rate.
- **Why HIGH/CRITICAL only:** triaging a LOW alert to closure without a
  formal investigation record is normal practice.

---

#### `REPEATED_ALERT_WITHOUT_REMEDIATION` — `ROOT-CAUSE-RECURRENCE-001`
- **Capability area:** Incident Response · **Weight:** 2.0
- **Reads:** `alerts.asset_id`, `alerts.category`, `cases.resolution_reason`
- **Fires when:** one asset carries repeated alerts of one category and
  no case closure records remediation or containment.
- **Configured:** `repeated_alert_min_occurrences = 3` ·
  `repeated_alert_remediation_keywords = ['remediated', 'contained',
  'blocked', 'patched', 'isolated', 'quarantined']`
- **Why `resolution_reason` and not `actions`:** the actions table
  records that an analyst acted, not that a root cause was fixed.
- **Limitation:** absence of a remediation *record* is not proof no
  remediation occurred.

---

### Negative space (5)

---

#### `TELEMETRY_GAP` — `TELEMETRY-COVERAGE-001`
- **Capability area:** Threat Detection · **Weight:** 3.0
- **Reads:** `telemetry.expected`, `telemetry.coverage_percentage`
- **Fires when:** an expected telemetry source reports coverage below
  the required threshold.
- **Configured:** `min_expected_coverage_pct = 70`
- **Grain:** one finding per source.

---

#### `MISSING_ALERT_CATEGORY` — `CATEGORY-PEER-COVERAGE-001`
- **Capability area:** Threat Detection · **Weight:** 1.5
- **Fires when:** an alert category present for a strong majority of
  peers was not observed for this entity.
- **Configured:** `missing_category_peer_presence_fraction = 0.6`
- **Limitation:** may reflect a different technology footprint rather
  than a monitoring gap. It is an indicator for review.

---

#### `LOW_ACTIVITY_OUTLIER` — `VOLUME-ZSCORE-001`
- **Capability area:** Cyber Resilience · **Weight:** 2.0
- **Fires when:** entity alert volume is a z-score outlier below the
  peer mean.
- **Configured:** `low_activity_zscore_threshold = -1.5`

---

#### `MISSING_ESCALATION_RECORDS` — `ESCALATION-RECORDKEEPING-001`
- **Capability area:** Governance and Oversight · **Weight:** 2.5
- **Fires when:** the fraction of an entity's HIGH/CRITICAL alerts
  carrying an escalation record of any kind falls below the threshold.
- **Configured:** `escalation_record_coverage_fraction = 0.5` ·
  `escalation_record_min_high_crit_alerts = 10`
- **Measures coverage, not total absence.** An earlier all-or-nothing
  version fired only on zero records and so never fired on real data: an
  entity logging 26 of 205 required records scored identically to a
  fully compliant one.
- **Distinct from `MISSED_ESCALATION`:** where no record exists, whether
  escalation happened cannot be determined either way. That is a
  record-keeping failure before it is an escalation failure.

---

#### `MISSING_INVESTIGATIONS` — `INVESTIGATION-RECORDKEEPING-001`
- **Capability area:** Governance and Oversight · **Weight:** 2.0
- **Fires when:** a majority of an entity's cases carry no investigation
  notes at all.
- **Configured:** `missing_investigation_min_cases = 5` ·
  `missing_investigation_empty_fraction = 0.5`
- **Distinct from `REPETITIVE_INVESTIGATION`**, which fires on
  duplicate-but-present notes.

---

## Review prioritisation

```
queue_priority  =  finding_weight  ×  severity_boost
case_priority   =  Σ queue_priority for findings on the same alert
```

Severity boosts: `CRITICAL 2.0 · HIGH 1.5 · MEDIUM 1.0 · LOW 0.5`.
Queue size: `review_queue.top_n = 25` **cases** — every finding in a
top-ranked case is shown, so a 4-finding case shows all four.

Findings sharing an alert are correlated into one case so a supervisor
sees compounding problems together. On the reference dataset, 81
prioritised findings correlate into 25 cases.

## Peer benchmarking

Entities are compared only within their own sector peer group, on rates
normalised per 100 alerts so a larger entity is not flagged for handling
more alerts. **An entity is excluded from its own peer median** — without
that, an outlier drags its own baseline toward itself.

Below **3 peers** the percentile and peer-median figures are withheld and
the group size is reported instead: a comparison against one or two peers
is not a benchmark.

## Capability mapping

All fourteen rules map to the eight capability areas the problem
statement names. The mapping lives in `capability_mapping` in the
configuration, carries a `why` for each assignment, and is a table
lookup — no model participates.

Each rule has exactly **one primary area**. A finding usually informs
several, but crediting it to several would double-count it in every
per-capability total and make the arithmetic uncheckable. Secondary
areas travel with the finding for context and are never scored.
