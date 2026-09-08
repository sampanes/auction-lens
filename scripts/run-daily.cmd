@echo off
REM The scheduled daily run. Point Windows Task Scheduler at this file.
REM
REM Every path this needs -- the config, the inbox, the database, the watchlist,
REM the .env -- is already a CLI default, so nothing is repeated here. What is
REM spelled out is only what this run decides: email it, and chase hunting lots.
setlocal

set "ROOT=%~dp0.."
set "AUCTION_LENS=%ROOT%\.venv\Scripts\auction-lens.exe"

if not exist "%AUCTION_LENS%" (
    echo [X] no installed application at %AUCTION_LENS%
    echo     follow "Start here" in README.md first
    exit /b 1
)

pushd "%ROOT%" || exit /b 1

REM Refuse before discovery if authorization or mail settings are incomplete.
"%AUCTION_LENS%" doctor --email
if errorlevel 1 goto :failed

REM Find today's lots, score them, and email what matters. Webhook delivery is
REM intentionally separate: enable and invoke it only when this installation
REM actually uses one.
"%AUCTION_LENS%" daily --email
if errorlevel 1 goto :failed

REM A separate errand: today's prices on the lots already being chased.
"%AUCTION_LENS%" watchlist --verdict hunting --email
if errorlevel 1 goto :failed

popd
exit /b 0

:failed
REM Task Scheduler should see the real code, not a flattened 1, and popd would
REM clear it, so read it before restoring the directory.
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%
