@echo off
REM Scheduled daily run: discover and email today's findings, then email hunting lots.
REM Point Windows Task Scheduler at this file. Any failed command is returned to it.
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [X] no virtual environment at %PYTHON%
    echo     follow the README quick start first
    exit /b 1
)

pushd "%ROOT%" || exit /b 1

"%PYTHON%" -m auction_lens daily ^
    --config "config\local.toml" ^
    --output "data\inbox\listings.json" ^
    --database "data\auction-lens.sqlite3" ^
    --watchlist "private\watchlist.json" ^
    --env-file ".env" ^
    --email
if errorlevel 1 goto :failed

"%PYTHON%" -m auction_lens watchlist ^
    --watchlist "private\watchlist.json" ^
    --verdict hunting ^
    --config "config\local.toml" ^
    --env-file ".env" ^
    --email
if errorlevel 1 goto :failed

popd
exit /b 0

:failed
set "EXIT_CODE=%errorlevel%"
popd
exit /b %EXIT_CODE%
