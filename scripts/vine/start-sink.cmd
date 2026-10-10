@echo off
rem Start the Vine companion sink in its own named window.
rem
rem Named so it can be found and stopped later, and idempotent so running this
rem twice leaves one sink rather than two fighting over the port.
setlocal
set TITLE=auction-lens-vine-sink
set PORT=8787
set ROOT=%~dp0..\..

rem taskkill exits 0 whether or not its filter matched anything, so its exit
rem code cannot answer "was one already running". Ask tasklist first, or the
rem operator gets told a sink was replaced every single time.
set WAS_RUNNING=
tasklist /FI "WINDOWTITLE eq %TITLE%*" /NH 2>nul | findstr /I /C:"python.exe" >nul && set WAS_RUNNING=1

if defined WAS_RUNNING (
    taskkill /FI "WINDOWTITLE eq %TITLE%*" /T /F >nul 2>&1
    echo [!] replaced a sink that was already running
)

if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [X] no virtual environment at %ROOT%\.venv
    endlocal
    exit /b 1
)

rem The sink's own default config is the auction provider's, whose search terms
rem were written for an auction warehouse. Serving those to Amazon's search box
rem would be a quiet mistake: the sweep would run, report success, and have
rem searched for the wrong things all evening. Name the Vine config here, and
rem refuse rather than fall back when it is missing.
set CONFIG=%ROOT%\config\vine.toml
if not exist "%CONFIG%" (
    echo [X] no config\vine.toml
    echo     copy config\providers\vine.example.toml config\vine.toml
    echo     then put your searches and income_tax_rate in it
    endlocal
    exit /b 1
)

rem %* comes last so a --config or --port passed to this script still wins.
start "%TITLE%" "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\vine\sink.py" --config "%CONFIG%" --port %PORT% %*
echo [OK] started %TITLE% on port %PORT%
echo.
echo   is it running?     tasklist /FI "WINDOWTITLE eq %TITLE%*"
echo   who has the port?  netstat -ano ^| findstr :%PORT% ^| findstr LISTENING
echo   stop it:           scripts\vine\stop-sink.cmd
endlocal
