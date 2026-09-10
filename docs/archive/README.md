# Archived — superseded approaches

Files here are kept for reference only. **They are not part of the
SAT-SA product and must not be packaged or documented as such.**

## `desktop_launcher-pywebview-superseded.py` + `BUILD-pywebview-superseded.md`

An earlier desktop approach that wrapped the Streamlit dashboard
(`app.py`) in a `pywebview` window and packaged it with PyInstaller.

Superseded by the native PySide6 application (`desktop.py` ->
`application/`). The pywebview route was dropped because it still ran
a Streamlit server on localhost inside the window — which conflicts
with the requirement that the product not be a browser/localhost
application, and which carried a WebKit system dependency that is
awkward in an air-gapped deployment.

The build instructions in the archived BUILD.md target that dead
launcher and will produce the wrong artifact if followed.
