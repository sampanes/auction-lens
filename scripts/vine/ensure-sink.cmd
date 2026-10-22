@echo off
rem Start the Vine companion sink only if nothing is serving its port.
rem
rem start-sink.cmd replaces a running sink, which is right after editing the
rem config and wrong on a schedule: replacing one mid-sweep ends that sweep.
rem This asks first, so the schedule can call it every night and a sink that
rem was closed during the day is back before a sweep falls due at midnight.
rem Measured: from 2026-10-11 to 2026-10-21 nothing was recorded, because the
rem sink window had been closed and nothing ever started it again.
rem
rem The question is the port rather than the window title, because a sink
rem started by hand in some other window answers the extension just as well.
setlocal
set PORT=8787
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo [OK] the sink is already serving port %PORT%
    endlocal
    exit /b 0
)
call "%~dp0start-sink.cmd"
endlocal
