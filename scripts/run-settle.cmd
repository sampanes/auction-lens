@echo off
REM The pass that reads what last night's lots actually sold for.
REM
REM Why this exists. Every other price this project stores is a reading taken
REM while bidding was still running, which makes it a floor and not a sale. A
REM closed lot leaves the search pages discovery reads -- across 2,580 cached
REM search rows not one closed lot appeared -- so no sweep can ever see a
REM result. The lot's own page can: it stays up after the close and states what
REM it went for. That is the only place a hammer price exists.
REM
REM Why that was worth a new kind of request. On one night's 2,810 closing lots,
REM 70% were last read more than thirty minutes before even their scheduled
REM close, and the error on those cannot be measured from readings alone,
REM because the lots with no late reading are exactly the ones whose late
REM movement went unseen. Lots also extend in thirty-second steps while bids
REM keep arriving, so the scheduled close is not the real one and nothing
REM before this recorded the real one at all.
REM
REM What it costs, and why that is gated. This asks for a page per lot, where a
REM search sweep describes forty lots per request. Permission to poll is not
REM permission to do that, so `settle_authorized` is a separate flag that
REM defaults to refusing, and `seconds_between_settles` paces it separately
REM from searches. Run this only with the provider's agreement to that load.
REM
REM Why it is not part of run-daily.cmd: it can only run once the last lot has
REM shut, by which time there is nothing to report and nobody to mail, and a
REM long run of requests failing should not take the night's mail down with it.
setlocal

set "ROOT=%~dp0.."
set "AUCTION_LENS=%ROOT%\.venv\Scripts\auction-lens.exe"

if not exist "%AUCTION_LENS%" (
    echo [X] no installed application at %AUCTION_LENS%
    echo     follow "Start here" in README.md first
    exit /b 1
)

pushd "%ROOT%" || exit /b 1

REM Refuse before the first request if provider authorization is incomplete.
REM No --email here on purpose: this run has nothing to send, and failing it
REM over an unset mail password would throw away the night's results.
"%AUCTION_LENS%" doctor
if errorlevel 1 goto :failed

REM Read the closes and store them. The counts go to stdout, which Task
REM Scheduler discards; the point of this run is the rows written.
"%AUCTION_LENS%" settle
if errorlevel 1 goto :failed

popd
exit /b 0

:failed
REM Task Scheduler should see the real code, not a flattened 1, and popd would
REM clear it, so read it before restoring the directory.
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%
