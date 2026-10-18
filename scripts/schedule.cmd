@echo off
REM The whole daily schedule, in one place, with three verbs.
REM
REM     schedule.cmd status     what is scheduled, when it next runs, how it ended
REM     schedule.cmd install    create or replace every task below
REM     schedule.cmd remove     delete them
REM
REM install is idempotent: /f replaces a task of the same name rather than
REM failing, so running it twice leaves exactly one of each.
REM
REM DEFAULT TIMES. Midday shows the board before bidding; three evening
REM passes refresh it across the hours lots actually close in. The first
REM evening task starts five minutes early because paced searches take time.
REM The private delivery ledger prevents an unchanged lot from repeating across
REM these overlapping passes.
REM
REM The last pass exists for the price history rather than for the mail. A
REM stored bid is only worth calling a closing price if it was read shortly
REM before the lot ended, and measured across 40,526 lots carrying history,
REM just 14% were. Lots close across four local hours and the last of them is
REM the busiest, so a schedule ending at 20:00 leaves the largest group of
REM closes permanently unobserved and every estimate built on them is a lower
REM bound wearing a price tag.
REM Change only the RUN declarations below when a different cadence fits better.
setlocal

set "ROOT=%~dp0.."
set "NELLIS=%ROOT%\scripts\run-daily.cmd"
set "PRICES=%ROOT%\scripts\run-prices.cmd"
set "SETTLE=%ROOT%\scripts\run-settle.cmd"
set "VINE=%ROOT%\scripts\vine\run-vine.cmd"
set "PREFIX=AuctionLens"

REM Task name, start time, which runner, and what the comment should say. One
REM line per run, so changing the schedule is changing this block and nothing
REM else. The runner is named per run because the auction provider and the Vine
REM companion are different workflows that only share a scheduler.
set "RUN_1=Daily|13:00|%NELLIS%|midday digest: the board before bidding starts"
set "RUN_2=Evening-1|17:55|%NELLIS%|first close: the board as the earliest lots go"
REM Four passes left one gap wide enough to lose a lot in. Measured across
REM 85,647 lots carrying an end time, 39.6% close between 18:00 and 20:00, and
REM nothing ran in between: the 19:00 hour alone holds 16,927 of them, read by
REM the 17:55 pass an average of 1.1 hours before they ended. A bid that old
REM is not a price, and a watchlist lot still under its estimate cannot be
REM reported as cheap by a pass that already happened.
REM
REM The odd-looking time is arithmetic, not taste. minimum_interval_minutes is
REM 60 and the poll ledger refuses a run strictly inside it, so a pass between
REM 17:55 and 20:00 has to clear an hour on both sides: anything from 18:56 to
REM 18:59. Tasks fire one to three seconds past the minute, so 19:00 exactly
REM would have been a coin flip that silently cost the 20:00 pass instead.
set "RUN_3=Evening-2|18:58|%NELLIS%|mid-close: the 19:00 hour, which 17:55 reads too early"
set "RUN_4=Evening-3|20:00|%NELLIS%|second pass: prices that have had time to mean something"
REM This one reads prices and does not mail, so it has its own runner.
REM On 2026-10-14 it went out as ordinary mail carrying 37 lots, of which
REM 30 closed the next day and the other 7 had under twelve minutes left:
REM nothing a reader could act on in either direction. The pass is still
REM wanted, for the reason above; the email never was. run-prices.cmd
REM carries the rest of the measurement.
set "RUN_5=Evening-4|21:35|%PRICES%|last look: records the prices no other pass is awake for"
REM Reads what last night's lots actually sold for, from each lot's own page.
REM It cannot run earlier: a lot extends in thirty-second steps while bids keep
REM arriving, so a lot asked at 21:35 may still be running, and nothing closes
REM after 22:00. Midnight leaves that settled and is still an hour clear of the
REM 21:35 pass, which the poll ledger requires.
REM
REM This is the one run that asks for a page per lot rather than forty lots per
REM page, so it refuses unless settle_authorized says the provider agreed to
REM that load. run-settle.cmd carries the measurement that motivated it.
set "RUN_6=Settle|00:05|%SETTLE%|reads what last night's lots really sold for"
REM The Vine run mails only if the companion swept today, which it does the
REM first time Chrome is open after local midnight. Seven leaves the night for
REM that to have happened, and it sends nothing rather than repeat yesterday.
set "RUN_7=Vine|07:00|%VINE%|morning Vine digest: what the companion found overnight"

if /i "%~1"=="status"  goto :status
if /i "%~1"=="install" goto :install
if /i "%~1"=="remove"  goto :remove
goto :usage


:status
echo Scheduled runs for %PREFIX%:
echo.
for %%R in ("%RUN_1%" "%RUN_2%" "%RUN_3%" "%RUN_4%" "%RUN_5%" "%RUN_6%" "%RUN_7%") do call :report %%R
echo.
echo Provider budget is in config/local.toml as max_requests_per_day.
goto :done


:install
if not exist "%NELLIS%" (
    echo [X] no runner at %NELLIS%
    exit /b 1
)
if not exist "%PRICES%" (
    echo [X] no runner at %PRICES%
    exit /b 1
)
if not exist "%SETTLE%" (
    echo [X] no runner at %SETTLE%
    exit /b 1
)
if not exist "%VINE%" (
    echo [X] no runner at %VINE%
    exit /b 1
)
for %%R in ("%RUN_1%" "%RUN_2%" "%RUN_3%" "%RUN_4%" "%RUN_5%" "%RUN_6%" "%RUN_7%") do call :create %%R
if errorlevel 1 exit /b 1
echo.
echo [OK] installed. Run "schedule.cmd status" to see the next fire times.
goto :done


:remove
for %%R in ("%RUN_1%" "%RUN_2%" "%RUN_3%" "%RUN_4%" "%RUN_5%" "%RUN_6%" "%RUN_7%") do call :destroy %%R
echo.
echo [OK] removed. Nothing will fetch on a schedule until you install again.
goto :done


REM ---- one task at a time -------------------------------------------------

:create
for /f "tokens=1,2,3,* delims=|" %%a in ("%~1") do (
    schtasks /create /f /tn "%PREFIX%-%%a" /tr "\"%%c\"" /sc DAILY ^
        /st %%b /ru "%USERNAME%" /it >nul
    if errorlevel 1 (
        echo [X] could not create %PREFIX%-%%a
        exit /b 1
    )
    echo [OK] %PREFIX%-%%a at %%b -- %%d
    REM schtasks cannot express an execution time limit, and this is the one
    REM thing worth having that it cannot say: a run that hangs on a slow
    REM response should not still be holding the provider an hour later.
    powershell -NoProfile -Command ^
        "$t = Get-ScheduledTask -TaskName '%PREFIX%-%%a';" ^
        "$t.Settings.ExecutionTimeLimit = 'PT1H';" ^
        "Set-ScheduledTask -InputObject $t | Out-Null" >nul 2>&1
)
exit /b 0


:destroy
for /f "tokens=1 delims=|" %%a in ("%~1") do (
    schtasks /delete /f /tn "%PREFIX%-%%a" >nul 2>&1
    if errorlevel 1 (
        echo [--] %PREFIX%-%%a was not scheduled
    ) else (
        echo [OK] deleted %PREFIX%-%%a
    )
)
exit /b 0


:report
for /f "tokens=1,2,3,* delims=|" %%a in ("%~1") do (
    schtasks /query /tn "%PREFIX%-%%a" >nul 2>&1
    if errorlevel 1 (
        echo   [--] %PREFIX%-%%a  not scheduled  ^(wanted %%b^)
    ) else (
        echo   [OK] %PREFIX%-%%a  %%d
        schtasks /query /tn "%PREFIX%-%%a" /fo LIST /v ^
            | findstr /b /c:"Next Run Time" /c:"Last Run Time" /c:"Last Result" /c:"Status"
    )
)
exit /b 0


:usage
echo Usage: schedule.cmd [status^|install^|remove]
echo.
echo   status   what is scheduled, when it next runs, how it last ended
echo   install  create or replace every run this file declares
echo   remove   delete them
exit /b 1


:done
endlocal
exit /b 0
