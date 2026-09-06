@echo off
REM Use the project venv to run the same check driver that CI runs.
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

"%PYTHON%" "%ROOT%\scripts\check.py"
exit /b %errorlevel%
