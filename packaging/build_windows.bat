@echo off
REM ------------------------------------------------------------------------------------------
REM  Builds BarcodeRedactor.exe (single file) and a portable folder on Windows 10/11.
REM  Requires Python 3.11-3.12 (64-bit) on the BUILD machine only. End users need nothing.
REM  Usage: double-click, or run from a terminal:   packaging\build_windows.bat
REM ------------------------------------------------------------------------------------------
setlocal
cd /d "%~dp0\.."

set PY=python
where py >nul 2>nul && set PY=py -3.12

echo === Creating build environment ===
%PY% -m venv .venv-build || goto :fail
call .venv-build\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt -r requirements-build.txt || goto :fail

echo === Generating icon ===
python tools\make_icon.py || goto :fail

echo === Running tests ===
set QT_QPA_PLATFORM=offscreen
python -m pytest -q tests || goto :fail
set QT_QPA_PLATFORM=

echo === Building single-file executable ===
set BR_ONEDIR=
pyinstaller --noconfirm --clean --workpath build\onefile packaging\BarcodeRedactor.spec || goto :fail

echo === Building portable folder ===
set BR_ONEDIR=1
pyinstaller --noconfirm --clean --workpath build\onedir packaging\BarcodeRedactor.spec || goto :fail
set BR_ONEDIR=
copy /y README.md dist\BarcodeRedactor_portable\ >nul
copy /y THIRD_PARTY_LICENSES.md dist\BarcodeRedactor_portable\ >nul
copy /y THIRD_PARTY_LICENSES.md dist\ >nul

echo === Self-test of the built executables ===
start "" /wait dist\BarcodeRedactor.exe --self-test --out dist\selftest_onefile.txt
if errorlevel 1 (type dist\selftest_onefile.txt & goto :fail)
type dist\selftest_onefile.txt
start "" /wait dist\BarcodeRedactor_portable\BarcodeRedactor.exe --self-test --out dist\selftest_portable.txt
if errorlevel 1 (type dist\selftest_portable.txt & goto :fail)

echo.
echo SUCCESS:
echo   dist\BarcodeRedactor.exe                  (single portable file)
echo   dist\BarcodeRedactor_portable\            (portable folder - starts faster)
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
