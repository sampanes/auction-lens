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
set "LOG=%ROOT%\private\settle-run.log"

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
>>"%LOG%" echo ==== %DATE% %TIME% settling run starting
echo     logging this run to %LOG%

REM Refuse before the first request if provider authorization is incomplete.
REM No --email here on purpose: this run has nothing to send, and failing it
REM over an unset mail password would throw away the night's results.
"%AUCTION_LENS%" doctor >>"%LOG%" 2>&1
if errorlevel 1 goto :failed

REM Read the closes and store them. The counts go to stdout, which Task
REM Scheduler discards; the point of this run is the rows written.
REM
REM The limit is the measurement from 2026-10-19, not caution. Asking for all
REM 2,720 of a night's closes got a connection closed without an answer about
REM seventeen minutes in, every reading before it discarded, and exit 2 in the
REM scheduler; five lots retried by hand were all dropped at the transport
REM level while the parser read a real captured product page without trouble.
REM So the provider is declining the volume rather than the request. At the
REM configured two seconds apart, 300 lots is ten minutes of polite asking and
REM roughly 2,100 settled prices a week, which is what every price range in
REM the reports is built from. Raise it by agreement with the provider, not
REM because a night looks quiet.
"%AUCTION_LENS%" settle --limit 300 >>"%LOG%" 2>&1
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
