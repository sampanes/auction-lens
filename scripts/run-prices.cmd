@echo off
REM The last scheduled pass of the night, which exists to read prices and not
REM to mail. Point Windows Task Scheduler at this file through schedule.cmd.
REM
REM Why this is a separate runner rather than a flag on run-daily.cmd: the two
REM are different errands that happen to share a schedule, exactly as the Vine
REM companion does. This one does not deliver, so it must not be gated on mail
REM settings either -- doctor is asked the question this run can actually fail,
REM which is authorization, and not the one it cannot, which is email.
REM
REM What it is for. Lots close across four local hours and the last of them is
REM the busiest, so a stored bid is only worth calling a closing price if some
REM pass read it shortly before the lot ended. That is this pass. Measured
REM across 40,526 lots carrying history, just 14% were read that late.
REM
REM Why it does not mail, measured on 2026-10-14: the 21:35 pass sent 37 lots,
REM and 30 of them closed the NEXT day while the other 7 had between one and
REM twelve minutes left. Not one was actionable -- too late to bid on tonight's
REM and no reason to read tomorrow's tonight, since the passes at 13:00, 17:55
REM and 18:58 reach them while they are still open. The reader learned nothing
REM and the mail cost its credibility. Recording the price needs no recipient.
setlocal

set "ROOT=%~dp0.."
set "AUCTION_LENS=%ROOT%\.venv\Scripts\auction-lens.exe"

if not exist "%AUCTION_LENS%" (
    echo [X] no installed application at %AUCTION_LENS%
    echo     follow "Start here" in README.md first
    exit /b 1
)

pushd "%ROOT%" || exit /b 1

REM Refuse before discovery if provider authorization is incomplete. No
REM --email here on purpose: this run has nothing to send, and failing it over
REM an unset mail password would throw away the night's closing prices.
"%AUCTION_LENS%" doctor
if errorlevel 1 goto :failed

REM Collect, score, and record. The report is still rendered to stdout, which
REM Task Scheduler discards; the point of this run is the observation written
REM to the database and the watchlist.
"%AUCTION_LENS%" daily
if errorlevel 1 goto :failed

popd
exit /b 0

:failed
REM Task Scheduler should see the real code, not a flattened 1, and popd would
REM clear it, so read it before restoring the directory.
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%
