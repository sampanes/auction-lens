@echo off
REM Run every check that CI runs, in the same order, against the project venv.
REM Usage:  scripts\test.cmd
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [X] no virtual environment at %PYTHON%
    echo     create one:   python -m venv .venv
    echo     then install: .venv\Scripts\python.exe -m pip install -e .[dev]
    exit /b 1
)

pushd "%ROOT%"

echo [1/5] compiling source and tests
"%PYTHON%" -m compileall -q src tests
if errorlevel 1 goto :failed

echo [2/5] checking that tracked text files are ASCII
"%PYTHON%" scripts\check-ascii.py
if errorlevel 1 goto :failed

echo [3/5] checking module layering
"%PYTHON%" scripts\check-imports.py
if errorlevel 1 goto :failed

echo [4/5] linting
"%PYTHON%" -m ruff check src tests scripts
if errorlevel 1 goto :failed

echo [5/5] running tests
"%PYTHON%" -m unittest discover -s tests
if errorlevel 1 goto :failed

popd
echo [OK] every check passed
exit /b 0

:failed
popd
echo [X] a check failed; see the output above
exit /b 1
