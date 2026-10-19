@echo off
REM The scheduled daily run. Point Windows Task Scheduler at this file.
REM
REM Every path this needs -- the config, the inbox, the database, the watchlist,
REM the .env -- is already a CLI default, so nothing is repeated here. What is
REM spelled out is only what this run decides: email it, and chase hunting lots.
REM
REM schedule.cmd owns every actual run time and points each task at this file.
REM Runs may see the same lots: the private delivery ledger is what keeps a
REM later email from repeating one the recipient already accepted at that bid.
setlocal

set "ROOT=%~dp0.."
set "AUCTION_LENS=%ROOT%\.venv\Scripts\auction-lens.exe"
set "LOG=%ROOT%\private\daily-run.log"

if not exist "%AUCTION_LENS%" (
    echo [X] no installed application at %AUCTION_LENS%
    echo     follow "Start here" in README.md first
    exit /b 1
)

pushd "%ROOT%" || exit /b 1

REM Everything below is written to a log as well as attempted, because these
REM runs are unattended: without one, a failure leaves nothing but Task
REM Scheduler's "Last Result" and the reason is gone by morning.
REM
REM The log is proven writable before anything is redirected into it. A
REM redirect to a path cmd cannot open does not fail the line -- it stops the
REM command from running at all and leaves errorlevel 0, so an unusable log
REM would turn an unattended run into a silent success that did nothing.
REM Measured with a file standing where the directory should be. The trailing
REM backslash is what tells a directory from a file, and the probe runs in a
REM child cmd because only there does a failed redirect set an exit code.
if not exist "%ROOT%\private\" mkdir "%ROOT%\private" 2>nul
cmd /c "type nul >>""%LOG%"""
if errorlevel 1 goto :nolog
>>"%LOG%" echo ==== %DATE% %TIME% digest run starting
echo     logging this run to %LOG%

REM Refuse before discovery if authorization or mail settings are incomplete.
"%AUCTION_LENS%" doctor --email >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

REM Find today's lots, score them, and email what matters. Webhook delivery is
REM intentionally separate: enable and invoke it only when this installation
REM actually uses one.
"%AUCTION_LENS%" daily --email >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

REM A separate errand: today's prices on the lots already being chased.
"%AUCTION_LENS%" watchlist --verdict hunting --email >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

>>"%LOG%" echo ---- %DATE% %TIME% completed
popd
exit /b 0

:nolog
REM Refusing here is the point. Every command this runner exists for redirects
REM into that log, and a redirect cmd cannot open silently skips the command it
REM was attached to rather than failing it.
echo [X] cannot write the run log at %LOG%
popd
exit /b 1

:failed
REM Task Scheduler should see the real code, not a flattened 1, and popd would
REM clear it, so read it before restoring the directory.
set "EXIT_CODE=%errorlevel%"
>>"%LOG%" echo ---- %DATE% %TIME% FAILED with %EXIT_CODE%
popd
exit /b %EXIT_CODE%
