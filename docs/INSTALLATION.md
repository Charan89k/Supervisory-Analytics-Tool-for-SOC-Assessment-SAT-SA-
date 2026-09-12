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

### What to download

SAT-SA is a **one-directory** PyInstaller build. `SAT-SA.exe` is 11 MB of a
179 MB application — the other 168 MB is the `_internal\` folder beside it,
holding Python, Qt, the Qt platform plugins and the rule configuration.

```
SAT-SA-Windows.zip
└── SAT-SA/
    ├── SAT-SA.exe            11 MB   the launcher
    └── _internal/           168 MB   Python, Qt, plugins, config  (889 files)
```

**Download the whole folder, not just the .exe.** Copying `SAT-SA.exe` on its
own produces an application that does not start — verified: run alone it hangs
with no output; with `_internal\` beside it, `--self-check` passes all five
checks. The executable locates everything relative to its own directory.

If the package also ships the optional AI installer and the notes, they sit
beside the executable:

```
SAT-SA/
├── SAT-SA.exe
├── _internal/
├── SAT-SA-Setup-AI.ps1       optional local AI setup
├── INSTALL.txt
└── README.txt
```

Keep the folder intact. Extract it somewhere writable and run the `.exe` from
inside it.

### Where to get it

Windows builds are published as GitHub Release assets, built by
`.github/workflows/windows-release.yml` on a real `windows-latest` runner that
runs the test suite and `--self-check` on the binary before attaching it. A tag
is a source marker; the release is what carries the download. No release has
been published yet, so this route is not yet live.

If no release has been published for the version you want, build one yourself
on Windows following [../packaging/BUILD.md](../packaging/BUILD.md).

The distribution route:

```
GitHub repository  ->  Releases  ->  the SAT-SA Windows release
                   ->  download SAT-SA-Windows.zip
                   ->  extract, keeping the folder intact
                   ->  run SAT-SA\SAT-SA.exe
```

With that in place a judge or evaluator would not need to clone the repository
or install Python at all. Until it is, they do.

### First run

```
SAT-SA.exe
   │
   ▼
SAT-SA starts and is immediately usable
   │
   ▼
It checks for Ollama and qwen2.5:3b
   │
   ├── both present ──────────►  LOCAL AI READY
   │
   └── something missing ─────►  optional setup offered
                                    ├── [Install Local AI]  (asks first)
                                    └── [Continue Without AI]
```

The check is a filesystem lookup and one loopback request. It does not block
startup, and nothing is installed or downloaded by it.

**Neither Ollama nor the Qwen model is inside the executable.** They are
separate software obtained once, on request. That is why the package is
~180 MB rather than several gigabytes.

- SAT-SA's assessment works fully without Ollama.
- It works fully without Qwen.
- Local AI is optional, and **Continue Without AI** changes nothing about an
  assessment: same findings, same evidence, same severities, same ranking,
  same reports.
- Installing Ollama or the model **requires internet, once**.
- After installation, inference is entirely local. No assessment data reaches
  any external service, before or after.

### Requirements

| | |
|---|---|
| OS | Windows 10 or 11, 64-bit (the build targets x86-64) |
| Disk | ~180 MB extracted, plus ~2 GB if you install the Qwen 3B model |
| RAM | 4 GB for the assessment; ~4 GB more while the 3B model is loaded |
| Python | **not required** — it is inside the package |
| Administrator | **not required** for SAT-SA; the Ollama installer may ask |
| Internet | only to install Ollama or download a model |
| GPU | not required. SAT-SA's analytics are CPU-only and use no GPU at all. Ollama will use a supported GPU if present, which affects only how fast explanations generate |

Figures for the assessment engine are measured on an 8th-generation Intel Core
i5 U-series laptop; the model figures are Ollama's own requirements and are not
something this project has benchmarked on Windows.

### Verification status — read this before distributing

**The Windows executable has been built and exercised under Wine on Linux. It
has not been run on a real Windows machine.**

Established under Wine: the binary is a genuine `PE32+ x86-64`, the GUI
launches and renders the dashboard, `--self-check` passes all five checks
reporting `platform: Windows 10 (AMD64)` with Windows paths resolved, and the
full assessment produces output identical to Linux.

**Not** established, and still needing a real Windows machine:

- launching from Explorer, and Windows Defender / SmartScreen behaviour
- code signing (the build is unsigned)
- the Local AI setup flow's PowerShell installation path — Wine does not
  exercise the vendor installer meaningfully
- installing Ollama, pulling Qwen, and a real explanation end to end
- report generation and file permissions under a real user profile
- DPI scaling and multi-monitor behaviour

Wine verification is a development aid. It is not native Windows validation,
and this package should be tested on Windows before it is given to anyone.


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

The `.exe` must be built **on Windows** — PyInstaller does not
cross-compile. From the project root, on a Windows machine:

```powershell
.\packaging\build_windows.ps1
```

| Parameter | Effect |
|---|---|
| `-Clean` | remove `build\` and `dist\` first; use after editing the spec |
| `-SkipTests` | skip the suite (not recommended — it is what catches a packaging change that broke an import) |

The script refuses to run on anything but Windows, refuses to build from a
failing test suite, and runs `SAT-SA.exe --self-check` against the finished
build — a folder that cannot pass its own self-check is a failed build, not a
shippable one. It then stages `SAT-SA-Setup-AI.ps1`, `INSTALL.txt` and
`README.txt` beside the executable.

Output is `dist\SAT-SA\` — a folder, 179 MB. Zipped for distribution it is
76 MB; the CI workflow zips it for you.

**Verification status as of v0.9.1.** A Windows PE32+ build has been produced
from this spec and passes `SAT-SA.exe --self-check`, but under Wine, not on
Windows — that exercises the import graph, bundled resources and Qt plugin
loading, and is not the same as a native run. The CI workflow that builds on a
real `windows-latest` runner is committed but has not yet been executed. See
[../packaging/BUILD.md](../packaging/BUILD.md).

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

### Two paths through Windows installation

**Path A — without Local AI.** Extract, launch, choose *Continue Without AI*,
assess. Every finding, score, evidence record and report is produced without a
model. Nothing further is needed and nothing is downloaded.

```
Extract  ->  SAT-SA.exe  ->  [Continue Without AI]  ->  run an assessment
```

**Path B — with Local AI.** The same, then let SAT-SA set the model up.

```
Extract  ->  SAT-SA.exe  ->  SAT-SA checks Ollama and qwen2.5:3b
                              │
                              ├─ both present  ->  LOCAL AI READY
                              │
                              └─ something missing
                                     ->  [Install Local AI]   (asks first)
                                     ->  downloads and verifies
                                     ->  LOCAL AI READY
```

You do not have to type a PowerShell command. SAT-SA invokes the reviewed
`SAT-SA-Setup-AI.ps1` that ships beside the executable, passing
`-Model qwen2.5:3b -AllowDownload` and a process-scoped execution-policy
bypass that expires with the process. The machine-wide policy is not changed.

The dialog tells you what will happen before it happens:

- **Start Ollama** touches no network.
- **Install Local AI** and **Install Qwen 2.5 3B** download from the internet,
  and the dialog says so, along with the fact that your assessment data stays
  on the machine.

If setup fails you get the real reason, plus **Retry** and **Continue Without
AI**. SAT-SA itself is unaffected either way — a failed AI setup has never
stopped an assessment.

The dialog is also available later from **Help → Set up Local AI…**

### Doing it yourself instead

If you would rather not have SAT-SA run anything, the setup script is a plain
file you can read and run directly. It is idempotent — run it twice and the
second run reports what is already in place and changes nothing.

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
is running: explanations begin only when a supervisor asks for one.

### Asking for an explanation

| Action | Where | Scope |
|---|---|---|
| **Explain with Local AI** | the finding detail panel, beside the AI heading | the one finding on screen |
| **Explain Top Findings** | the Review Queue | the top N queued findings, cancellable |

Either way the model receives that finding, its evidence, its position in the
queue, the other findings on the same alert, and its entity's context — and
nothing else. The dataset is never sent.

The explanation appears in its own panel, headed **AI EXPLANATION —
&lt;model&gt;**, with the deterministic finding, rule and evidence unchanged
above it. If the model cannot be reached the panel reads **AI EXPLANATION
UNAVAILABLE**, says why, and the assessment is unaffected.

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
pytest                                              # 611 tests
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
