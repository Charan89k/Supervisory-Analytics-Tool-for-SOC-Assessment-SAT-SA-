# SAT-SA — Offline / Air-Gapped Deployment

SAT-SA is built to run in an NCIIPC-controlled environment with no
Internet connectivity. This is a design constraint, not a configuration
option.

## What the application requires

```
  No Internet                 No cloud service
  No external AI API          No SaaS dependency
  No remote telemetry         No update check
```

**Verified, not asserted.** A grep across `analytics/`, `application/`,
`main.py`, `desktop.py` and `config/` for any `http(s)://` URL returns
exactly one result:

```
http://localhost        ← the optional local AI runtime, on this machine
```

A loopback address. Traffic to it does not leave the host. (Tests and
the setup script also use the numeric form `127.0.0.1`; both name this
machine.)

This check is run at the end of every development phase.

## Two things, kept separate

The distinction that matters for an air-gapped deployment:

| | Internet to prepare | Internet to run |
|---|---|---|
| **SAT-SA core** — ingestion, validation, detection, evidence, scoring, benchmarking, review queue, reports | **no** | **no** |
| **Optional local AI — inference** | — | **no**, once installed |
| **Optional local AI — first-time installation** of Ollama or a model | **yes**, unless the files are supplied on the media | no |

The assessment engine never needs a network. The only step that can is
*obtaining* the runtime and the model in the first place, and that step
is optional, explicitly consented to, and one-time.

SAT-SA does not download anything on its own. The in-application setup
dialog states before it acts whether the action reaches the network —
starting an already-installed service does not; fetching a runtime or a
model does — and `SAT-SA-Setup-AI.ps1` contacts nothing unless passed
`-AllowDownload`.

For a machine that will never have a network, supply the files on the
media instead: see the `ai\` folder in `packaging/BUILD.md`, or use the
llama.cpp path below, where the model is a data file you copy.

## Verifying it yourself

```bash
# 1. Every URL in the product code
grep -rEho "https?://[a-zA-Z0-9._-]+" --include=*.py --include=*.yaml \
    analytics/ application/ main.py desktop.py config/ | sort -u

# 2. Run with networking disabled
sudo ip link set <interface> down      # or unplug / disable Wi-Fi
python desktop.py                      # assess a dataset end to end
python -m pytest                       # the suite makes no network call
```

The test suite and UI smoke test both force
`SATSA_NARRATION_BACKEND=mock`, so no automated run can contact a
backend or load a model.

## Transferring to an air-gapped machine

**1. Application** — the repository, or a packaged build.

**2. Python dependencies.** On a connected machine of the same platform:

```bash
pip download -r requirements-desktop.txt -d wheels/
```

Copy `wheels/` across, then on the target:

```bash
python -m venv venv && source venv/bin/activate
pip install --no-index --find-links wheels/ -r requirements-desktop.txt
```

**3. AI runtime and model (optional).** Either the files listed in
`packaging/BUILD.md` for the Ollama path, or a `.gguf` file copied as
data for the llama.cpp path. See below.

**4. Dataset.** The submission itself.

## The local model: two supported paths

SAT-SA supports two local inference paths. Both run entirely on the
assessment machine and neither contacts the internet. They differ in
what has to be installed and approved, and that difference is the whole
basis for choosing between them.

| | Ollama (default) | llama.cpp (strict air-gap) |
|---|---|---|
| What is installed | a background service | a Python library |
| Runtime | a resident daemon on a loopback TCP port | in-process, no port |
| Model provisioning | the runtime's own mechanism | a file you copy |
| Configuration by the operator | none — discovered | one file path |
| Accreditation surface | service + daemon + provisioning | one data file |

### Ollama — the default, because it needs no configuration

The shipped default. SAT-SA discovers the runtime and the model itself:
the operator enters no executable path, no model directory, no endpoint
and no GGUF path. The whole procedure is to run `SAT-SA-Setup-AI.ps1`
once and start the application.

The setup script installs from files shipped alongside it, so this path
works on a machine with no internet. It verifies the installer's
published SHA-256 before running it, pins the runtime to `127.0.0.1`,
and changes no firewall rule.

**Ollama is the easiest default. It is not automatically appropriate for
every air-gapped or accredited environment.** It installs a resident
background service that listens on a local TCP port and provisions
models through a mechanism of its own. Those are three separate things
for an accreditation to review, and some environments will not permit a
resident service on an assessment host at all. Whether Ollama is
acceptable is a decision for the accrediting authority for that
environment — not something this tool can assert on their behalf.

### llama.cpp — where a resident service is not permitted

Fully supported and not deprecated. `LlamaCppBackend` loads a plain
GGUF **data file** in-process: no service, no daemon, no listening port,
no separate provisioning mechanism. The model travels on the same media
as the dataset and is replaced by replacing the file.

Prefer this path where a background service or a listening port is not
permitted, or where the accreditation position is simpler to argue for
a data file than for installed software.

```bash
pip install --no-index --find-links wheels/ llama-cpp-python
mkdir -p /opt/sat-sa/models
cp qwen2.5-7b-instruct-q4_k_m.gguf /opt/sat-sa/models/
```

Then set the backend in `config/assessment_rules.yaml`:

```yaml
llm_narration:
  backend: "llamacpp"
  model_path: "/opt/sat-sa/models/qwen2.5-7b-instruct-q4_k_m.gguf"
```

The model-file picker appears in **Settings → AI** only on this path,
where a file genuinely has to be named. Status should read
**AI READY**.

### Choosing

```
DEFAULT          SAT-SA -> Ollama -> Qwen 2.5 7B      (zero configuration)
STRICT AIR-GAP   SAT-SA -> llama.cpp -> local GGUF    (no service, no port)
NO AI            deterministic analytics, unchanged
```

**The assessment must not require internet access under any of these,
and it does not.** The third row is a first-class option, not a
degraded one: the deterministic analytics are the product, and they are
identical in all three cases.

### Updating a model

On the llama.cpp path, replace the `.gguf` and restart. There is no
downloader, no registry, and no version negotiation. The path, file
size and modification time are reported through the status probe, so an
assessment can record which model artefact was in place.

On the Ollama path, re-run the setup script with the new model file in
`ai\`.

### Running without one

The application starts, assesses, and reports the specific reason AI is
unavailable — **AI NOT INSTALLED**, **AI NOT RUNNING**, **AI MODEL
MISSING** or **AI NOT CONFIGURED** — each with the one action that would
resolve it. Every finding keeps its rule-generated rationale.

**AI is off by default.** A fresh air-gapped install needs no model at
all, and nothing about the assessment changes without one.

## Hardware

**Deterministic assessment** (the product):

| | |
|---|---|
| CPU | any x86-64 or ARM64; the pipeline is single-process |
| RAM | 4 GB comfortably handles ~58,000 alerts (373 MB measured) |
| Disk | ~50 MB application; assessments are a few MB each |
| Display | required for the desktop app; `main.py` runs headless |

Measured on an 8th-generation Intel Core i5 U-series laptop, 20 GB RAM:
**3,296 alerts across 5 entities in 1.5s**, and **58,240 alerts across 30
entities in 21.0s** at 373 MB peak. A local benchmark, not a capacity
guarantee.

**Optional AI layer**, additional:

| Model | Additional RAM | CPU-only |
|---|---|---|
| Qwen 2.5 3B | ~2 GB | fastest |
| Qwen 2.5 7B | ~4.5 GB | ~1–4 min per explanation |
| Qwen 2.5 14B | ~9 GB | minutes per explanation |

Offline means local compute, **not local speed**.

## Where data lives

| What | Location | Override |
|---|---|---|
| Assessments | `assessments/<timestamp>/` | Settings, or `SATSA_ASSESSMENTS_DIR` |
| Settings | `~/.config/SAT-SA/settings.json` | `SATSA_CONFIG_DIR` |
| Rules | `config/assessment_rules.yaml` | `--config` |
| CLI output | `--out` | |

All local. Nothing is synchronised anywhere.

## Auditability in an air-gapped environment

- Every threshold is in one readable YAML file.
- Every finding carries its `rule_id`, rationale and evidence.
- Any finding drills down to the submitted rows behind it.
- Risk scores are linear and hand-recomputable.
- Settings are plain JSON.
- Assessments are immutable once written.
- The capability mapping is a table with a stated reason per entry.
