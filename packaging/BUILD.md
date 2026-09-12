# Building the SAT-SA package

## PyInstaller does not cross-compile

A Windows `SAT-SA.exe` must be built on Windows. Building on Linux
produces a Linux binary from the same spec — worth doing in CI or
during development, because it proves the import graph and the bundled
data are correct, but it is not the deliverable.

## Build

From the project root, on the target platform:

```
python -m venv venv
venv\Scripts\pip install -r requirements-desktop.txt pyinstaller
venv\Scripts\pyinstaller packaging\SAT-SA.spec --noconfirm
```

Output: `dist\SAT-SA\` — onedir, not onefile. A onefile executable
unpacks itself to a temporary directory on every launch, which costs
seconds each time and cannot be read by an accreditation review
without unpacking it first.

Verify immediately, on the build machine:

```
dist\SAT-SA\SAT-SA.exe --self-check
```

Every line must read PASS. The AI line is informational: a build
machine with no AI runtime is expected to report `AI NOT INSTALLED`,
and that is a pass.

## Assemble the package

```
SAT-SA-<version>-windows-x64\
    SAT-SA.exe
    _internal\...
    SAT-SA-Setup-AI.ps1        from packaging\
    INSTALL.txt                from packaging\
    README.txt                 from packaging\
    ai\                        optional, see below
```

## The optional `ai\` folder

Only needed if the package must enable AI on a machine with no
internet. Three files:

| File | What it is |
|---|---|
| `OllamaSetup.exe` | the runtime installer, from ollama.com |
| `OllamaSetup.exe.sha256` | its published SHA-256 |
| `qwen2.5:7b.gguf` | the model weights (~4.7 GB, Q4_K_M) |

`SAT-SA-Setup-AI.ps1` verifies the checksum before executing the
installer and refuses to continue on a mismatch. Ship the `.sha256`
file — without it the script warns that it cannot verify what it is
about to run.

Omit `ai\` entirely and the package is smaller and still complete: the
setup script then explains what to add, and SAT-SA runs without AI.

**Do not commit these files.** They are large third-party binaries;
they belong on the distribution media, not in the repository.

## Size

Roughly 320 MB unpacked, most of it Qt and the scientific Python
stack. The spec excludes what is genuinely unused, and each exclusion
was verified rather than assumed:

- **`pyarrow` (149 MB) is excluded.** pandas 3.x imports it
  opportunistically and does not need it for anything SAT-SA does. The
  full pipeline, including CSV and PDF export, was run with `pyarrow`
  blocked to confirm this.
- **PIL is NOT excluded**, though it looks equally unused. reportlab
  imports it at module load, so removing it breaks PDF export
  immediately — verified the same way.
- Qt WebEngine, Quick, QML, Multimedia, 3D and SQL are excluded.
  Shipping a browser engine inside an air-gapped desktop tool would be
  both an accreditation liability and about a gigabyte of dead weight.
- Streamlit and plotly are excluded: they belong to `app.py`, the
  legacy reference view, which is not the product.

If you add a dependency, re-run `--self-check` on the frozen build
before shipping. It is the cheapest way to catch a hidden import that
PyInstaller's static analysis could not follow.

## The Local AI setup flow, and what the package contains

The application can install Ollama and `qwen2.5:3b` for the user, on
request. **Neither is inside the executable.**

| | In the EXE | How it arrives |
|---|---|---|
| SAT-SA | yes | the package |
| Ollama runtime | **no** | the setup flow, or the user, or `ai\` on the media |
| Qwen model | **no** | the same, into Ollama's own store |

Bundling either would put a multi-gigabyte third-party runtime and model
inside a supervisory tool, take the package from ~180 MB to several
gigabytes, and make an optional convenience compulsory. The setup flow
exists precisely so the executable does not have to carry them.

The dialog delegates Windows installation to `SAT-SA-Setup-AI.ps1`, so
that script must be staged beside the executable — the build script
does this. Without it the dialog reports the script as missing and
points the user at ollama.com rather than failing silently.

### Hidden imports for the Local AI modules: not required

`application/ui.py` imports `application.widgets.local_ai_dialog` and
`application.services.local_ai_setup` **inside functions**, which looks
like the pattern that needs a hidden import. It was tested rather than
assumed: a build with no hints for either module contains both.
PyInstaller 6.22.2 walks bytecode and follows function-level imports on
its own.

The same test showed `analytics.narration.prompt` is also found without
its hint. That entry is kept as belt-and-braces, because losing it would
break explanations in the packaged build only. The Local AI modules are
deliberately *not* listed: a hidden import that is not needed is a claim
about the import graph that can quietly stop being true.

Re-run the check after adding a module that is only reached dynamically:

```bash
python - <<'PY'
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
a = CArchiveReader('dist/SAT-SA/SAT-SA.exe')      # or dist-windows/...
name = next(n for n in a.toc if str(n).endswith('.pyz'))
d = a.extract(name); open('/tmp/p.pyz','wb').write(d if isinstance(d, bytes) else d[1])
z = ZlibArchiveReader('/tmp/p.pyz')
for m in ("application.widgets.local_ai_dialog",
          "application.services.local_ai_setup"):
    print(m, "PRESENT" if m in z.toc else "MISSING")
PY
```

## What must never be bundled

Model weights in the executable, real SOC data, credentials, or any
outbound endpoint. The offline audit in `docs/TESTING.md` covers the
last of these; run it before tagging a release.
