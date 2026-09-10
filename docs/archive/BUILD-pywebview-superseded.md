# Building the SAT-SA Desktop Executable

`desktop_launcher.py` wraps the existing Streamlit dashboard
(`app.py`) in a native window using `pywebview`, so the end user sees
one application window — no browser, no typing `localhost:8501`. The
dashboard code itself is untouched.

## 1. Install the extra dependencies

```bash
source venv/bin/activate
pip install pywebview pyinstaller
```

On Linux, `pywebview` needs a WebKit backend. If it's missing:

```bash
# Debian/Ubuntu
sudo apt install python3-gi gir1.2-webkit2-4.1
# Arch
sudo pacman -S webkit2gtk-4.1
```

## 2. Test it unpackaged first

Before building the exe, always confirm the wrapper itself works:

```bash
python desktop_launcher.py
```

A window titled "SAT-SA" should open showing your dashboard. Close
the window and confirm the terminal returns cleanly (no leftover
`streamlit` process — check with `ps aux | grep streamlit`).

## 3. Package it

```bash
pyinstaller --noconfirm --onefile --name SAT-SA \
    --add-data "app.py:." \
    --add-data "analytics:analytics" \
    --add-data "config:config" \
    --add-data "schemas:schemas" \
    desktop_launcher.py
```

The output binary lands in `dist/SAT-SA` (Linux) or `dist\SAT-SA.exe`
(Windows — see note below).

Test the packaged binary the same way:

```bash
./dist/SAT-SA
```

## 4. About the `.exe`

PyInstaller does **not** cross-compile. Running the command above on
this Linux machine produces a Linux binary (`dist/SAT-SA`), not a
Windows `.exe` — that's a hard limitation of the tool, not a config
issue. To get an actual `.exe`, the same two commands (`pip install`
+ `pyinstaller ...`) need to be run on a Windows machine, with a
Windows Python install. The source code doesn't change at all between
the two — same repo, same launcher script, just run where the
Windows build target is.

A reasonable plan: keep developing and demoing on Linux with the
Linux binary day-to-day, and only do the Windows build once, close to
submission, on whatever machine you'll actually hand the `.exe` to
(a judge's laptop, a shared lab machine, etc.) — you don't need
Windows access for anything else in this project.

## 5. Common issues

- **Window opens but shows a Streamlit error / blank page**: usually
  means the Streamlit subprocess didn't fully start before the window
  tried to connect. Rerun — `desktop_launcher.py` waits up to 30s,
  but a cold PyInstaller-bundled Python can be slower on first launch.
- **Antivirus flags the `.exe`**: extremely common false positive for
  PyInstaller `--onefile` builds (it self-extracts at runtime, which
  looks similar to some malware behavior). Not a bug in your code —
  worth a line in your README/submission notes if a judge's machine
  flags it.
- **Charts/plotly not rendering in the packaged build**: if this
  happens, add `--collect-all plotly` to the `pyinstaller` command.
