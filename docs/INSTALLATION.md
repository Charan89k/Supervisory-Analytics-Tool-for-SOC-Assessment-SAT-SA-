# SAT-SA — Installation

Three ways to install, depending on what you need. All of them work with no
network connection once the files are in place.

| | For | Needs Python |
|---|---|---|
| [Desktop from source](#1-desktop-application-from-source) | developers, evaluators with Python | yes |
| [Headless engine](#2-headless-engine-only) | CI, servers, batch assessment | yes |
| [Packaged Windows build](#3-packaged-windows-build) | evaluators, air-gapped machines | **no** |

The optional AI layer is covered separately in
[section 4](#4-optional-local-ai). SAT-SA produces the same findings, scores
and reports without it.

---

## 1. Desktop application, from source

### Requirements

- Python 3.10 or newer (developed and tested on 3.14)
- A graphical display for the desktop application
- Roughly 500 MB of disk for the virtual environment

### Steps

```bash
git clone <repository-url> soc_advisory
cd soc_advisory

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements-desktop.txt
```

Generate a dataset to work with — the repository ships no data:

```bash
python data/generator/generate_dataset.py --socs 5 --alerts-per-soc 800 \
    --out data/synthetic
```

Verify, then launch:

```bash
python desktop.py --self-check
python desktop.py
```

### Linux system libraries

Qt needs platform libraries that most desktop installs already have. If the
window does not appear:

```bash
# Debian / Ubuntu
sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libegl1

# Arch
sudo pacman -S libxcb xcb-util-wm xcb-util-image xcb-util-keysyms \
    xcb-util-renderutil libxkbcommon-x11
```

To confirm the application itself is fine and only the display is missing:

```bash
QT_QPA_PLATFORM=offscreen python tests/smoke_ui.py
```

If that passes, SAT-SA works.

---

## 2. Headless engine only

`requirements.txt` installs the analytics engine with no Qt and no display
dependency — suitable for a server or CI runner.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

python main.py --data data/synthetic --out outputs --export-csv --export-pdf
```

| Flag | Effect |
|---|---|
| `--data` | input dataset directory (default `./data/synthetic`) |
| `--out` | where results are written (default `outputs`) |
| `--config` | rules file (default `config/assessment_rules.yaml`) |
| `--export-csv` | also write the flat CSV exports |
| `--export-pdf` | also write the PDF executive summary |
| `--validate` | measure detection against a labelled dataset's ground truth |
| `--trends` | assess each period separately and compare |
| `--narrate` | run the optional explanation layer |

---

## 3. Packaged Windows build

The build is a **folder**, not an installer. Nothing is written to the
registry, no Python is required, and SAT-SA itself needs no administrator
rights.

### Installing

```
1. Extract the package somewhere writable.
       Documents\SAT-SA is a good choice.
       Program Files is NOT: SAT-SA does not need elevation and never asks.

2. (Optional) Enable local AI — run once:
       Right-click SAT-SA-Setup-AI.ps1  →  Run with PowerShell

3. Verify:
       SAT-SA.exe --self-check

4. Launch:
       SAT-SA.exe
```

### If PowerShell blocks the script

Use a bypass scoped to that one process, which expires when the window closes:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\SAT-SA-Setup-AI.ps1
```

Do **not** run `Set-ExecutionPolicy Unrestricted` or otherwise change the
machine-wide policy. Nothing about SAT-SA requires weakening a system setting,
and the process-scoped form above leaves no lasting change.

### Where your data goes

| What | Location |
|---|---|
| Assessments | `Documents\SAT-SA\assessments\` — one immutable folder per run |
| Settings | `%APPDATA%\SAT-SA\settings.json` |
| Self-check report | `Documents\SAT-SA\self-check.txt` |
| Submissions | wherever you put them; SAT-SA reads and never modifies them |

Nothing is written inside the program folder, so the package can be replaced on
upgrade without losing a single assessment.

### Verifying an install

```
SAT-SA.exe --self-check
```

Reports the rule configuration, analytics engine, explanation backends,
writable storage and AI status, then writes the same report to
`Documents\SAT-SA\self-check.txt`. A packaged Windows build is a windowed
application with no console, so the file is the copy you can actually read —
and the one to attach to an accreditation record.

Every line must read `PASS`. The AI line is informational: a machine with no AI
runtime reports `AI NOT INSTALLED` and still passes.

### Building the package

The `.exe` must be built **on Windows** — PyInstaller does not cross-compile.
See [../packaging/BUILD.md](../packaging/BUILD.md).

---

## 4. Optional: local AI

SAT-SA assesses submissions without AI. The optional layer only restates
findings the rule engine has already decided, in plainer language. Three
supported configurations:

| | Runtime | Provisioning | Use when |
|---|---|---|---|
| **A. No AI** | — | — | the default |
| **B. Ollama** | background service, loopback port | `SAT-SA-Setup-AI.ps1` | easiest; nothing to configure |
| **C. llama.cpp** | in-process, no service | copy a `.gguf` file | a resident daemon is not permitted |

### B. Ollama (default, zero configuration)

Run the setup script once. It is idempotent — run it twice and the second run
reports what is already in place and changes nothing.

```powershell
.\SAT-SA-Setup-AI.ps1                        # offline, from the bundled ai\ folder
.\SAT-SA-Setup-AI.ps1 -Model qwen2.5:3b      # a different model tag
.\SAT-SA-Setup-AI.ps1 -AllowDownload         # permit fetching from ollama.com
```

| Parameter | Default | Effect |
|---|---|---|
| `-Model` | `qwen2.5:7b` | the model tag SAT-SA should resolve |
| `-AllowDownload` | off | permit contacting Ollama's official endpoints |

**Offline by default.** Without `-AllowDownload` the script installs only from
files shipped beside it in `ai\`:

```
ai\OllamaSetup.exe          the runtime installer
ai\OllamaSetup.exe.sha256   its published checksum
ai\qwen2.5:7b.gguf          the model weights
```

The checksum is verified before the installer is executed, and a mismatch stops
the script. If those files are absent and `-AllowDownload` was not given, the
script names exactly what to add and exits without changing anything.

What it deliberately does not do: expose the runtime to the network (it pins
`OLLAMA_HOST` to `127.0.0.1:11434`), add or modify a firewall rule, disable any
security control, fetch or execute a remote script, or send SOC data anywhere.

| Exit code | Meaning |
|---|---|
| 0 | ready |
| 2 | no runtime found and none bundled |
| 3 | bundled installer failed checksum verification |
| 4 | installer ran but the runtime was not found afterwards |
| 5 | the local service did not start, or stopped responding |
| 6 | the model could not be installed or resolved |

Every non-zero path ends by stating that SAT-SA still works without AI.

Once it succeeds, start SAT-SA. Nothing needs configuring: the runtime is
discovered through `PATH` and the platform's install locations, and the model
list comes from the service's own loopback API. The sidebar reads **AI READY**.

### C. llama.cpp (strict air-gap)

No service, no listening port, no separate provisioning mechanism — the model
is a plain data file loaded in-process.

```bash
pip install --no-index --find-links wheels/ llama-cpp-python
mkdir -p /opt/sat-sa/models
cp qwen2.5-7b-instruct-q4_k_m.gguf /opt/sat-sa/models/
```

Then in `config/assessment_rules.yaml`:

```yaml
llm_narration:
  enabled: true
  backend: "llamacpp"
  model_path: "/opt/sat-sa/models/qwen2.5-7b-instruct-q4_k_m.gguf"
```

The model-file picker appears in **Settings → AI** only on this path, where a
file genuinely has to be named. Status should read **AI READY**.

### Reading AI status

| Status | Meaning | What resolves it |
|---|---|---|
| `AI DISABLED` | switched off | enable it in Settings |
| `AI NOT INSTALLED` | no runtime found | run `SAT-SA-Setup-AI` once |
| `AI NOT RUNNING` | runtime present, service not responding | start the service |
| `AI MODEL MISSING` | service healthy, model absent | run `SAT-SA-Setup-AI` |
| `AI NOT CONFIGURED` | llama.cpp path, no `.gguf` selected | choose a file in Settings |
| `SAMPLE EXPLANATIONS` | the built-in sample explainer | — (no model runs) |
| `AI READY` | a real model is reachable | — |

`AI READY` means detected, present and reachable. It does **not** mean anything
is running: explanations begin only when a supervisor selects **Explain Top
Findings** on the Review Queue.

---

## 5. Environment variables

All optional. SAT-SA works with none of them set.

| Variable | Effect |
|---|---|
| `SATSA_NARRATION_BACKEND` | force a backend (`mock`, `ollama`, `llamacpp`); `mock` guarantees no model loads |
| `SATSA_ASSESSMENTS_DIR` | relocate assessment history |
| `SATSA_CONFIG_DIR` | relocate the settings file |
| `QT_QPA_PLATFORM=offscreen` | run the UI with no display, for tests |

---

## 6. Verifying the installation

```bash
pytest                                              # 541 tests
QT_QPA_PLATFORM=offscreen python tests/smoke_ui.py  # 112 UI checks
python desktop.py --self-check                      # installation check
```

No automated run loads a language model. See [TESTING.md](TESTING.md).

---

## 7. Uninstalling

**From source** — delete the clone and the virtual environment. Settings and
assessment history live outside it:

```
~/.config/SAT-SA/          settings   (Windows: %APPDATA%\SAT-SA\)
<project>/assessments/     assessment history
```

**Packaged** — delete the extracted folder. Your assessments in
`Documents\SAT-SA\` are deliberately left alone; remove that folder too if you
want them gone.

**Ollama**, if installed, is separate software and is uninstalled through
Windows' own "Apps & features". SAT-SA does not manage its lifecycle.
