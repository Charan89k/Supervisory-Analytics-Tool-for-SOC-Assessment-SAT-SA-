# SAT-SA — Local AI Explanation Layer

## The boundary

```
        Deterministic analytics
                  │
                  ▼
     Finding + Evidence + Severity + Risk
                  │
                  │   ◄── AUTHORITATIVE. Complete without any model.
                  │
                  ▼
        Optional local language model
                  │
                  ▼
        Human-readable explanation
                  │
                  ▼   ◄── SUPPLEMENTARY. Advisory prose, nothing more.
          Human examiner
```

**The model is not the decision engine.** It receives a finding the rule
engine has already decided and restates it in plainer prose. It cannot:

- create a finding
- remove a finding
- change a severity
- change a risk score
- change a queue position
- invent evidence, events, entities or metrics
- override anything deterministic

## How that is enforced

Not by convention — structurally, at three levels.

**1. It runs after the fact.** Narration begins only once the assessment
has completed and its outputs are written to disk. Nothing it does can
reach them.

**2. It receives a copy, not the record.** A backend is handed
`finding_view(record)` — a field-filtered deep copy. A backend that
mutates everything it is given changes nothing:

```
after a backend that rewrites severity, priority, rule_id and evidence:
  severity         'CRITICAL'        unchanged
  queue_priority   4.0               unchanged
  rule_id          'REAL-001'        unchanged
  evidence         {'minutes': 2.0}  unchanged
```

The view also excludes prior narration fields, so a backend cannot see
or echo back earlier output.

**3. It can only write narration fields.** The service assigns
`narrated_explanation`, `narration_source`, `narration_model`,
`narration_is_mock`, `narration_provenance` — and nothing else. A test
asserts the set of keys added.

**4. The UI keeps them apart.** The explanation lives in its own widget,
below and outside the authoritative detail panel. There is no code path
by which narration text can be written into the panel showing the rule,
rationale and evidence. The smoke test asserts, on real data, that
narration text is absent from the authoritative panel while the
deterministic rationale and evidence remain visible beside it.

## What the model actually receives

The prompt is the only channel from SAT-SA to the model, and it is
one-way. It is built by `analytics/narration/prompt.py` from the
finding the deterministic engine already decided, and it is
**deterministic**: the same finding always produces the same prompt, so
a prompt can be reproduced and audited beside the explanation it
generated.

A field that is absent is **omitted**, never rendered as a blank or a
placeholder — a blank beside a label is something a model can narrate
as a fact.

```
=== FINDING (decided by the deterministic rule engine) ===
- Finding type: ACK_WITHOUT_INVESTIGATION
- Rule / detector: INVESTIGATION-ABSENT-001
- Severity (decided by the rule engine): CRITICAL
- Entity (CSE): SOC-003
- Organisation: Finance CSE 003
- Sector / peer group: FINANCE
- Alert ID: ALT-SOC-003-000230
- Case ID: CASE-ALT-SOC-003-000230
- Assigned analyst ID: ANL-SOC-003-001

=== WHY THE RULE FIRED (the engine's own rationale) ===
Severity CRITICAL alert was acknowledged and later closed, but no
investigation was ever started on it...

=== EVIDENCE (the recorded facts behind this finding) ===
- ack_delay_minutes: 51.6
- acknowledged: True
- closed: True
- expected_lifecycle_event: INVESTIGATION_STARTED
- investigation_started: False

=== SUPERVISORY PRIORITY ===
- Position in the supervisory review queue: 1
- Findings correlated onto this same alert: 4

=== OTHER FINDINGS ON THIS SAME ALERT ===
- REPETITIVE_INVESTIGATION (TEMPLATE-INVESTIGATION-001)
- MISSING_EVIDENCE (EVIDENCE-REQUIRED-001)
- SLOW_TRIAGE (ACK-SLA-001)

=== ENTITY CONTEXT ===
- Entity supervisory risk score: 142.438
- Percentile within its peer group: 100.0

=== EVIDENCE COMPLETENESS FOR THIS ENTITY ===
- Records available for the checks that need them: 85%
```

Only the evidence for **that one finding** is sent. The dataset is never
handed to the model.

### A regression worth recording

`finding_view()` assembled fourteen fields for the model and the
original `build_prompt()` formatted **six**, silently dropping the alert
id, the case id, the analyst and every ranking field. Nothing failed:
the model simply never learned which alert it was describing, so it
could only paraphrase the rule's own rationale back and every
explanation read much the same. `tests/test_prompt_pipeline.py` asserts
what actually reaches the model, not merely that a prompt was produced.

## Anti-hallucination

Two layers, and the structural one is what actually holds.

**Structural.** The backend is handed a defensive deep copy and the
service writes back only five `narration_*` fields. A backend that
rewrites severity, priority, rule id or evidence on the object it was
given changes nothing, because that object is discarded. A test hands
it a deliberately hostile backend and asserts exactly that.

**Instructional.** The prompt states the rules the model is asked to
follow:

- use only the supplied finding and evidence;
- never invent evidence, alert IDs, case IDs, analyst IDs, timestamps,
  counts or metrics;
- never state that an incident, breach or compromise occurred — the
  evidence describes how alerts were *handled*, not whether an attack
  succeeded;
- never change a severity, score or priority; never create, merge,
  split or dismiss a finding;
- if the evidence is insufficient, say so rather than filling the gap;
- distinguish **missing evidence** from **evidence of failure** — an
  absent record means the activity cannot be confirmed either way;
- negative-space findings are indicators for review, not proof of
  non-compliance;
- write about the entity and its process, never about a named
  individual's competence.

### Output validation

`validate_explanation()` rejects an empty response, one too short to be
an explanation, and one that echoed the instructions back. A rejected
response is **reported as a failure, not stored** — putting a truncation
beside a finding would place text there that reads as analysis and is
not.

It is deliberately shallow. It cannot detect a confidently wrong
statement, and pretending otherwise would be worse than not checking.
The real defence is that the examiner always has the deterministic
finding and its evidence on screen beside the explanation.

## If the model is unavailable

| Situation | Result |
|---|---|
| AI disabled | Assessment completes. Every finding keeps its rule rationale. |
| No AI runtime installed | Status reads **AI NOT INSTALLED**. Assessment completes. |
| Runtime installed, not running | Status reads **AI NOT RUNNING**. Assessment completes. |
| Runtime running, model absent | Status reads **AI MODEL MISSING**. Assessment completes. |
| No `.gguf` selected (llama.cpp) | Status reads **AI NOT CONFIGURED**. Assessment completes. |
| Model file missing (llama.cpp) | Status reads **AI UNAVAILABLE**. Assessment completes. |
| Backend crashes | Caught and reported as a warning. The assessment is already saved. |
| Narration cancelled | Partial explanations are kept; everything else keeps its rule rationale. |

Each state carries the single action that would resolve it
(`BackendStatus.remedy()`), defined once so every surface — settings
page, status tile, self-check report — gives the same instruction.

**AI is off by default.** A fresh install produces a complete assessment
with no model present and no configuration.

## Backends

```
LocalLLMBackend
├── OllamaBackend     DEFAULT — zero-configuration, discovered runtime
├── LlamaCppBackend   STRICT AIR-GAP — a local .gguf file, no service
└── MockBackend       tests and demo — deterministic samples, no model
```

Both real backends run entirely on the assessment machine. Neither
contacts the internet. They differ in what has to be installed and
approved, which is the only basis for choosing between them.

### Ollama is the default because it asks the operator for nothing

`analytics/narration/ollama_runtime.py` discovers the runtime: the
executable through `PATH` and then the platform's install locations,
and the model list through the service's own loopback API. SAT-SA never
reads Ollama's blob store — it asks the service what it has, so it is
not coupled to another tool's on-disk layout.

No path, no model directory, no endpoint, no `.gguf` location is ever
requested from the user. Discovery is strictly read-only: it installs
nothing, starts nothing, and downloads nothing, and a test asserts the
module has no mechanism to.

**This does not make Ollama suitable for every environment.** It
installs a resident background service on a local TCP port with its own
model provisioning. Whether that is acceptable is a decision for the
accrediting authority — see `OFFLINE_DEPLOYMENT.md`.

### llama.cpp remains fully supported

Not deprecated, and not a fallback. `llama.cpp` is an **in-process
library** reading a single `.gguf` file from a path the operator
controls: no service, no daemon, no listening port. The model becomes a
**data file that travels on the same media as the dataset**, not
installed software. Updating it means replacing the file and restarting.

Use it wherever a resident service is not permitted.

`llama-cpp-python` is an optional import. An install without it starts,
assesses, and reports AI status honestly.

### Status states

Distinct states, because they need different responses. Collapsing them
into one "unavailable" would leave an operator with a red light and no
idea what to do about it:

| State | Meaning | What resolves it |
|---|---|---|
| `AI DISABLED` | Turned off. No backend is contacted at all. | Enable it in Settings |
| `AI NOT INSTALLED` | No AI runtime found on this machine. | Run `SAT-SA-Setup-AI` once |
| `AI NOT RUNNING` | Runtime installed; its service is not responding. | Start the service |
| `AI MODEL MISSING` | Service healthy; the configured model is absent. | Run `SAT-SA-Setup-AI` |
| `AI NOT CONFIGURED` | llama.cpp path with no model file selected. | Select a `.gguf` in Settings |
| `AI UNAVAILABLE` | Configured, but unusable for another reason. | See the detail text |
| `SAMPLE EXPLANATIONS` | The mock explainer — no language model running. | — |
| `AI READY` | A real model is ready. | — |

**`AI READY` means detected, present and reachable — not that anything
is running.** It is a statement about availability; narration begins
only when a supervisor asks for it.

The availability probe has its **own short timeout** (3s) separate from
the generation timeout (180s), so a dead backend reports itself dead in
about a second rather than freezing the window for minutes. Probing
never loads model weights.

## Two ways to ask for an explanation

| | Where | Scope | Runs |
|---|---|---|---|
| **Explain Top Findings** | Review Queue | the top N queued findings | threaded, cancellable, progress bar |
| **Explain with Local AI** | the finding detail panel | the one finding on screen | on the UI thread behind a wait cursor |

The second exists because an examiner reading one case should not have
to wait for ten explanations to get the one in front of them. It
returns a structured outcome — success, explanation, backend, model,
finding id, error — so the caller can tell "the model said this" from
"the model could not be reached" and show the right thing either way.

On failure the panel reads **AI EXPLANATION UNAVAILABLE**, states why,
and says the deterministic assessment is unaffected. The finding, its
rule and its evidence stay on screen above, unchanged.

## Explanations are requested, never automatic

An assessment takes seconds. Explaining ten findings on a CPU takes
minutes. Running the second automatically after the first would make
every assessment feel like it takes minutes, for output the supervisor
may not have wanted — and for output that, by construction, cannot
change the result.

So it does not happen. Completing an assessment reports that
explanations are *available*; it does not start them.

```
  RUN ASSESSMENT  ──►  findings, scores, reports    (seconds)
                          │
                          ▼
                  Review Queue
                          │
                          ▼
          [ Explain Top Findings ]  ◄── the supervisor decides
                          │
                          ▼
                  explanations, cancellable at any point
```

The action lives on the Review Queue, beside the findings it explains,
with the progress and cancel controls. `narration_blocker()` decides in
one place whether it can run, so the button's enabled state, its
tooltip and its refusal message cannot disagree with each other.

**AI availability is automatic; AI execution is not.** Detection of the
runtime happens on its own, in the background, against a short timeout.
Generation happens when someone asks.

## Scope — what actually gets explained

An assessment produces roughly 1,500 findings. Explaining all of them at
1–4 minutes each on CPU is hours of compute for output nobody reads.

```
  ~1,481 findings
        │
        ▼
     84 prioritised review-queue items
        │
        ▼
     10 explained    ◄── max_explanations, the default
```

**Scope is bounded by queue rank and an explicit maximum, not by
severity.** The review queue is already ~100% CRITICAL/HIGH by
construction — queue priority *is* weight × severity boost — so a
severity filter bounds essentially nothing. The Settings page says so
rather than offering severity as though it were the real lever.

The dashboard shows both numbers together (`10 / 1,481`), so a small
explained slice cannot be mistaken for the whole assessment.

## Sample explanations are labelled as samples

`MockBackend` exists so the whole narration pipeline can be developed
and tested without executing a multi-gigabyte model. It never presents
itself as one:

- `is_mock=True`, `model="deterministic-sample"`
- text opens with `[SAMPLE EXPLANATION]`
- provenance states *"no language model involved"*
- the UI heading reads **SAMPLE EXPLANATION — NO LANGUAGE MODEL**

This labelling is not decoration. The output feeds regulatory
assessment; text that reads as model-generated analysis but came from a
template would misrepresent the basis of a finding.

## Configuration

`llm_narration` in `config/assessment_rules.yaml`:

| Key | Default | Effect |
|---|---|---|
| `enabled` | `false` | Master switch |
| `backend` | `ollama` | `ollama` (default) · `llamacpp` · `mock` |
| `model` | `qwen2.5:7b` | Model identifier for Ollama |
| `model_path` | `""` | Path to a local `.gguf` for llama.cpp |
| `temperature` | `0.1` | Low keeps prose close to the evidence |
| `timeout_seconds` | `180` | Generation budget |
| `availability_timeout_seconds` | `3` | Probe budget |
| `max_explanations` | `10` | The lever that bounds the work |
| `severity_scope` | `critical_high` | Secondary; see above |
| `max_queue_rank` | `null` | Optional rank cut-off |

An **absent** `backend` key resolves to the default — that is what makes
the shipped product zero-configuration. A **misspelled** one resolves to
the sample explainer instead, which labels its own output: a typo must
not silently start a real model under a name nobody wrote.

`SATSA_NARRATION_BACKEND=mock` overrides the configured backend, so no
automated run can load a real model. The test suite asserts this in
`conftest.py` rather than relying on the configured default, and the UI
smoke test sets it before any import.

**`qwen2.5:14b` is never the default.** It needs roughly 9 GB resident
and is unusable on modest hardware; a default that hangs the machine it
ships on is not a default. It remains selectable as a deliberate choice.

## Hardware

| Model | Resident | CPU-only speed |
|---|---|---|
| Qwen 2.5 3B | ~2 GB | fastest of the three |
| Qwen 2.5 7B | ~4.5 GB | roughly 1–4 minutes per explanation |
| Qwen 2.5 14B | ~9 GB | minutes per explanation; needs sizing for |

Offline means **local compute, not local speed**. A 14B model does the
same matrix arithmetic locally that it would in a datacentre, on
whatever CPU is in the machine.

## Known limitation

`LlamaCppBackend.explain()` has **not been executed against a real
model**. It is structurally sound and its configuration handling is
tested, but the generation path itself is unrun. Treat it as
configuration-verified rather than model-verified until exercised on
hardware with a `.gguf` present.
