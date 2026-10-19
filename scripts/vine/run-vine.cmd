@echo off
REM The scheduled Vine run: read what the companion recorded, score it, mail it.
REM
REM This is the back half only. Nothing here asks Amazon for anything -- both
REM Vine discovery callables raise, so no schedule can make them. The front
REM half is the browser companion, which sweeps once a day while Chrome happens
REM to be open and tells the sink when it finished.
REM
REM scripts\schedule.cmd owns the actual run time and points the task here.
setlocal

set "ROOT=%~dp0..\.."
set "AUCTION_LENS=%ROOT%\.venv\Scripts\auction-lens.exe"
set "CONFIG=%ROOT%\config\vine.toml"
set "RECORDS=%ROOT%\private\vine"
set "FRESH_RECORDS=%ROOT%\private\vine-fresh.ndjson"
set "LISTINGS=%ROOT%\private\vine-listings.json"
set "STAMP=%RECORDS%\last-sweep.json"
set "LOG=%ROOT%\private\vine-run.log"

if not exist "%AUCTION_LENS%" (
    echo [X] no installed application at %AUCTION_LENS%
    exit /b 1
)
if not exist "%CONFIG%" (
    echo [X] no config\vine.toml
    echo     copy config\providers\vine.example.toml config\vine.toml
    exit /b 1
)

pushd "%ROOT%" || exit /b 1

REM Everything below is written to the log as well as attempted. This run is
REM the one nobody sees: it mails at seven in the morning and its own commands
REM wrote to a console Task Scheduler discards, so a failure recorded its exit
REM code here and never the reason for it.
REM
REM The log is proven writable before anything is redirected into it. A
REM redirect to a path cmd cannot open does not fail the line -- it stops the
REM command from running at all and leaves errorlevel 0, so an unusable log
REM would turn this into a silent success that mailed nothing. The trailing
REM backslash is what tells a directory from a file, and the probe runs in a
REM child cmd because only there does a failed redirect set an exit code.
if not exist "%ROOT%\private\" mkdir "%ROOT%\private" 2>nul
cmd /c "type nul >>""%LOG%"""
if errorlevel 1 goto :nolog
>>"%LOG%" echo ==== %DATE% %TIME% fresh-record run starting
echo     logging this run to %LOG%

REM Refuse before anything else if the mail settings or the judge are not
REM ready. This is the check that makes an unattended run safe to schedule.
"%AUCTION_LENS%" doctor --config "%CONFIG%" --email >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

REM Has anything arrived lately? Chrome closed for a day means no fresh
REM records, and a digest of nothing new is worse than silence. Silence with
REM no trace is how a dead pipeline goes unnoticed for weeks, so a skip is
REM logged.
REM
REM Two earlier versions of this gate asked something else and each silenced
REM the mail completely. Asking whether a sweep ran 'today' rejected the
REM freshest records there are, because a sweep is an evening activity and
REM this runs in the morning. Asking whether a sweep *finished* was no better:
REM Amazon throttles one after a term or two and it abandons the attempt, so a
REM night that recorded hundreds of rows reported nothing to send.
REM
REM An exit code, not a captured string. Capturing output here means nesting
REM quotes inside a `for /f`, which is exactly what the first version of this
REM got wrong: both values came out empty, empty compared equal to empty, and
REM the gate let a run through on the one morning it existed to stop.
"%ROOT%\.venv\Scripts\python.exe" "%ROOT%\scripts\vine\fresh-records.py" --output "%FRESH_RECORDS%" >>"%LOG%" 2>&1
if errorlevel 1 (
    echo     sending nothing; the companion records when Chrome is next open
    >>"%LOG%" echo ---- %DATE% %TIME% nothing fresh recorded, nothing sent
    popd
    exit /b 0
)

REM Collapse only fresh sightings into one lot per item, then score and mail.
REM The private delivery ledger is what stops a lot already accepted at this
REM value from being mailed a second time.
"%AUCTION_LENS%" pull --config "%CONFIG%" --input "%FRESH_RECORDS%" --output "%LISTINGS%" >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

"%AUCTION_LENS%" run --config "%CONFIG%" --input "%LISTINGS%" --email >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

>>"%LOG%" echo ---- %DATE% %TIME% fresh-record run completed
popd
exit /b 0

:nolog
REM Refusing here is the point. Every command this runner exists for
REM redirects into that log, and a redirect cmd cannot open silently skips
REM the command it was attached to rather than failing it.
echo [X] cannot write the run log at %LOG%
popd
exit /b 1

:failed
REM Task Scheduler should see the real code, not a flattened 1, and popd would
REM clear it, so read it before restoring the directory.
set "EXIT_CODE=%errorlevel%"
>>"%LOG%" echo %DATE% %TIME% failed with %EXIT_CODE%
popd
exit /b %EXIT_CODE%
