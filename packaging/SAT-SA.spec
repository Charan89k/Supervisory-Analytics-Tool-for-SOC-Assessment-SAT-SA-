# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for SAT-SA.

Build (from the project root, on the TARGET platform):

    pyinstaller packaging/SAT-SA.spec --noconfirm

PyInstaller does not cross-compile. A Windows SAT-SA.exe must be built
on Windows; building here produces a Linux binary, which is still worth
doing because it proves the import graph and bundled data are correct.

ONEDIR, not onefile, and deliberately so:

  * A onefile executable unpacks itself to a temporary directory on
    every launch — several seconds of delay each time, and a different
    path each run.
  * An accreditation review can read a directory. It cannot read a
    self-extracting blob without unpacking it first.

Nothing here reaches the network at build time or at run time.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve().parent

block_cipher = None

# ---------------------------------------------------------------------
# Bundled read-only resources
# ---------------------------------------------------------------------
# The rule configuration is the assessment's published contract: an
# examiner must be able to read the thresholds the tool applied. It
# ships as a plain YAML file inside the bundle rather than being
# compiled in, so it can be inspected and, where a sector requires
# different thresholds, replaced.
datas = [
    (str(PROJECT_ROOT / "config"), "config"),
    (str(PROJECT_ROOT / "docs" / "OFFLINE_DEPLOYMENT.md"), "docs"),
    (str(PROJECT_ROOT / "docs" / "AI.md"), "docs"),
]

# ---------------------------------------------------------------------
# Imports PyInstaller's static analysis cannot see
# ---------------------------------------------------------------------
hiddenimports = [
    # Backends are resolved by name from configuration, so nothing
    # imports them literally. Without these the AI layer would report
    # itself unavailable in a packaged build for no visible reason.
    "analytics.narration.llamacpp_backend",
    "analytics.narration.mock",
    "analytics.narration.ollama_backend",
    "analytics.narration.ollama_runtime",
    # Ingestion adapters are likewise selected at runtime by format.
    *collect_submodules("analytics.ingestion"),
    # reportlab loads its font and graphics machinery dynamically.
    "reportlab.graphics.barcode",
    "reportlab.pdfbase._fontdata",
]

# ---------------------------------------------------------------------
# Excluded: size, and honesty about what ships
# ---------------------------------------------------------------------
# Streamlit and plotly belong to app.py, the legacy reference view that
# is NOT the product. The Qt modules below are pulled in by PySide6's
# dependency graph and never used. Shipping a web server and a browser
# engine inside an air-gapped desktop tool would be an accreditation
# liability and roughly a gigabyte of dead weight.
# pyarrow is 149 MB — roughly a third of the unexcluded bundle. pandas
# 3.x imports it opportunistically and does not need it for anything
# SAT-SA does; the full pipeline, including CSV and PDF export, was run
# with pyarrow blocked to confirm that before excluding it.
#
# PIL is NOT excluded, despite looking equally unused: reportlab imports
# it at module load (reportlab.lib.utils), so removing it breaks PDF
# export immediately. Verified the same way. Do not "optimise" it out.
excludes = [
    "pyarrow",
    "streamlit", "plotly", "altair", "tornado",
    "matplotlib", "IPython", "jupyter", "notebook",
    "pytest", "_pytest",
    "tkinter", "test", "unittest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtDataVisualization", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtDBus",
]

a = Analysis(
    [str(PROJECT_ROOT / "desktop.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SAT-SA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip endpoint protection
    console=False,      # a GUI application, not a console tool
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SAT-SA",
)
