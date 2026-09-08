@echo off
REM Friendly CMD entry point. PowerShell does the secure password prompt and
REM careful file updates; this wrapper keeps the normal Windows workflow simple.
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-gmail.ps1" %*
exit /b %errorlevel%
