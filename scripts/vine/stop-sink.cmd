@echo off
rem Stop the Vine companion sink and say whether the port came free.
setlocal
set TITLE=auction-lens-vine-sink
set PORT=8787

rem taskkill exits 0 even when its filter matched nothing, so asking tasklist
rem is the only way to report honestly what this script actually stopped.
set WAS_RUNNING=
tasklist /FI "WINDOWTITLE eq %TITLE%*" /NH 2>nul | findstr /I /C:"python.exe" >nul && set WAS_RUNNING=1

if defined WAS_RUNNING (
    taskkill /FI "WINDOWTITLE eq %TITLE%*" /T /F >nul 2>&1
    echo [OK] stopped %TITLE%
) else (
    echo [!] no window titled %TITLE% was running
)

echo.
echo anything still listening on %PORT%?
rem Only LISTENING matters here. Recently closed connections linger in
rem TIME_WAIT and would otherwise read as a port that never comes free.
netstat -ano | findstr :%PORT% | findstr LISTENING
if errorlevel 1 echo [OK] port %PORT% is clear
endlocal
