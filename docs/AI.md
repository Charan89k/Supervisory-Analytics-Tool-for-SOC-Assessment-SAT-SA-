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
| No model installed | Status reads **AI NOT CONFIGURED**. Assessment completes. |
| Model file missing | Status reads **AI UNAVAILABLE**. Assessment completes; the skip is logged. |
| Backend crashes | Caught and reported as a warning. The assessment is already saved. |
| Narration cancelled | Partial explanations are kept; everything else keeps its rule rationale. |

**AI is off by default.** A fresh install produces a complete assessment
with no model present and no configuration.

## Backends

```
LocalLLMBackend
├── LlamaCppBackend   SHIPPED — a local .gguf file, no service
├── OllamaBackend     development convenience on a workstation
└── MockBackend       tests and demo — deterministic samples, no model
```

### Why llama.cpp rather than Ollama for deployment

Ollama is a **background service**: it needs separate installation, a
running daemon on a TCP port, and its own out-of-band model
provisioning — three things an air-gapped accreditation must approve.

`llama.cpp` is an **in-process library** reading a single `.gguf` file
from a path the operator controls. The model becomes a **data file that
travels on the same media as the dataset**, not installed software.
Updating it means replacing the file and restarting.

`llama-cpp-python` is an optional import. An install without it starts,
assesses, and reports AI status honestly.

### Status states

Four distinct states, because they need different responses:

| State | Meaning |
|---|---|
| `AI DISABLED` | Turned off. No backend is contacted at all. |
| `AI NOT CONFIGURED` | No model file selected, or the library is absent. |
| `AI UNAVAILABLE` | Configured, but the model is missing or unreachable. |
| `SAMPLE EXPLANATIONS` | The mock explainer — no language model running. |
| `AI AVAILABLE` | A real model is ready. |

The availability probe has its **own short timeout** (3s) separate from
the generation timeout (180s), so a dead backend reports itself dead in
about a second rather than freezing the window for minutes. Probing
never loads model weights.

## Scope — what actually gets explained

An assessment produces roughly 1,500 findings. Explaining all of them at
1–4 minutes each on CPU is hours of compute for output nobody reads.

```
  ~1,481 findings
        │
        ▼
     81 prioritised review-queue items
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
| `backend` | `mock` | `llamacpp` · `ollama` · `mock` |
| `model` | `qwen2.5:7b` | Model identifier for Ollama |
| `model_path` | `""` | Path to a local `.gguf` for llama.cpp |
| `temperature` | `0.1` | Low keeps prose close to the evidence |
| `timeout_seconds` | `180` | Generation budget |
| `availability_timeout_seconds` | `3` | Probe budget |
| `max_explanations` | `10` | The lever that bounds the work |
| `severity_scope` | `critical_high` | Secondary; see above |
| `max_queue_rank` | `null` | Optional rank cut-off |

`SATSA_NARRATION_BACKEND=mock` overrides the configured backend, so no
automated run can load a real model. The test suite and UI smoke test
both set it.

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
