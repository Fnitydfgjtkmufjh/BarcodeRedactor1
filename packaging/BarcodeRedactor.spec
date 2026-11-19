# PyInstaller spec for Barcode Redactor.
#   Single-file portable exe:   pyinstaller packaging/BarcodeRedactor.spec
#   Portable folder (onedir):   set BR_ONEDIR=1 then run the same command
# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ONEDIR = os.environ.get("BR_ONEDIR") == "1"

datas = [
    (str(ROOT / "barcode_redactor" / "resources"), "barcode_redactor/resources"),
    (str(ROOT / "THIRD_PARTY_LICENSES.md"), "."),
    (str(ROOT / "LICENSE"), "."),
]
binaries = []
hiddenimports = ["zxingcpp"]
for pkg in ("pypdfium2", "pypdfium2_raw"):
    try:
        datas += collect_data_files(pkg)
        binaries += collect_dynamic_libs(pkg)
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "reportlab", "PyQt5", "PyQt6",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtNetwork", "PySide6.QtOpenGL",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
common = dict(
    name="BarcodeRedactor",
    debug=False,
    strip=False,
    upx=False,  # UPX breaks Qt plugins and triggers antivirus false positives
    console=False,  # windowed app: no terminal window
    icon=str(ROOT / "barcode_redactor" / "resources" / "icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="BarcodeRedactor_portable")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
