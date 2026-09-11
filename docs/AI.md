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
