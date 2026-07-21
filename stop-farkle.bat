@echo off
rem Stop the Farkle Optimiser started by start-farkle.vbs (server on port 8000).
setlocal enabledelayedexpansion
set "found="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  taskkill /F /T /PID %%a >nul 2>&1
  set "found=1"
)
if defined found ( echo Farkle Optimiser stopped. ) else ( echo It does not look like it is running. )
pause
