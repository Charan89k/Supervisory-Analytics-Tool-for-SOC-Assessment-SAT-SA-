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
http://localhost        ← the optional Ollama development backend
```

This check is run at the end of every development phase.

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

**3. Model (optional).** A `.gguf` file, copied as data. See below.

**4. Dataset.** The submission itself.

## The local model

The shipped inference path is **llama.cpp reading a local `.gguf`
file** — chosen precisely for this environment.

| | Ollama | llama.cpp |
|---|---|---|
| What is installed | a background service | a Python library |
| Runtime | a daemon on a TCP port | in-process |
| Model provisioning | its own out-of-band mechanism | a file you copy |
| Accreditation surface | service + daemon + provisioning | one data file |

The model is a **data file that travels on the same media as the
dataset**, not installed software.

### Installing a model

```bash
pip install --no-index --find-links wheels/ llama-cpp-python
mkdir -p /opt/sat-sa/models
cp qwen2.5-7b-instruct-q4_k_m.gguf /opt/sat-sa/models/
```

Then in **Settings → AI**: backend `Local model file (llama.cpp)`,
browse to the file, Save. Status should read **AI AVAILABLE**.

### Updating a model

Replace the `.gguf` and restart. There is no downloader, no registry,
and no version negotiation. The path, file size and modification time
are reported through the status probe, so an assessment can record which
model artefact was in place.

### Running without one

The application starts, assesses, and reports **AI NOT CONFIGURED**.
Every finding keeps its rule-generated rationale. **AI is off by
default** — a fresh air-gapped install needs no model at all.

## Hardware

**Deterministic assessment** (the product):

| | |
|---|---|
| CPU | any x86-64 or ARM64; the pipeline is single-process |
| RAM | 4 GB comfortably handles ~28,000 alerts |
| Disk | ~50 MB application; assessments are a few MB each |
| Display | required for the desktop app; `main.py` runs headless |

Measured: **3,296 alerts in 1.6s**; **27,800 alerts across 12 entities in
9.2s**, on an 8th-generation Intel Core i5 U-series laptop.

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
