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
REM WHY THESE TIMES. Nothing on this provider closes before 18:00 or after
REM 22:00, and the lots are spread almost evenly across those four hours, so
REM there is no peak to aim at. Scored over 1009 lots the interests claim --
REM a run landing on a lot's own close counts 1, one four hours early counts 0
REM -- 13:00 scores zero, because at 13:00 the nearest close is five hours out.
REM A single evening run peaks at 18:00 and captures 49% of what is there. A
REM second at 20:00 takes it to 74%, and a third buys only nine points more.
REM
REM 17:55 rather than 18:00 because the run itself takes about six minutes:
REM searches are paced ten seconds apart, so starting at 17:55 puts the data in
REM hand as the first lots go rather than after.
setlocal

set "ROOT=%~dp0.."
set "RUNNER=%ROOT%\scripts\run-daily.cmd"
set "PREFIX=AuctionLens"

REM Task name, start time, and what the comment should say. One line per run,
REM so changing the schedule is changing this block and nothing else.
set "RUN_1=Daily|13:00|midday digest: the board before bidding starts"
set "RUN_2=Evening-1|17:55|first close: the board as the earliest lots go"
set "RUN_3=Evening-2|20:00|second pass: prices that have had time to mean something"

if /i "%~1"=="status"  goto :status
if /i "%~1"=="install" goto :install
if /i "%~1"=="remove"  goto :remove
goto :usage


:status
echo Scheduled runs for %PREFIX%:
echo.
for %%R in ("%RUN_1%" "%RUN_2%" "%RUN_3%") do call :report %%R
echo.
echo Provider budget is in config/local.toml as max_requests_per_day.
goto :done


:install
if not exist "%RUNNER%" (
    echo [X] no runner at %RUNNER%
    exit /b 1
)
for %%R in ("%RUN_1%" "%RUN_2%" "%RUN_3%") do call :create %%R
if errorlevel 1 exit /b 1
echo.
echo [OK] installed. Run "schedule.cmd status" to see the next fire times.
goto :done


:remove
for %%R in ("%RUN_1%" "%RUN_2%" "%RUN_3%") do call :destroy %%R
echo.
echo [OK] removed. Nothing will fetch on a schedule until you install again.
goto :done


REM ---- one task at a time -------------------------------------------------

:create
for /f "tokens=1,2,* delims=|" %%a in ("%~1") do (
    schtasks /create /f /tn "%PREFIX%-%%a" /tr "\"%RUNNER%\"" /sc DAILY ^
        /st %%b /ru "%USERNAME%" /it >nul
    if errorlevel 1 (
        echo [X] could not create %PREFIX%-%%a
        exit /b 1
    )
    echo [OK] %PREFIX%-%%a at %%b -- %%c
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
for /f "tokens=1,2,* delims=|" %%a in ("%~1") do (
    schtasks /query /tn "%PREFIX%-%%a" >nul 2>&1
    if errorlevel 1 (
        echo   [--] %PREFIX%-%%a  not scheduled  ^(wanted %%b^)
    ) else (
        echo   [OK] %PREFIX%-%%a  %%c
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
