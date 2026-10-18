@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Autonomous Laboratory v6 - Self Test
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Environment not built yet. Please run run.bat once first.
  echo.
  pause
  endlocal
  exit /b 1
)

echo.
echo   Running end-to-end self test with the built-in simulator
echo   ^(no Arduino required^)...
echo.
".venv\Scripts\python.exe" -X utf8 tools\selftest.py
set "SELFTEST_EXIT=%ERRORLEVEL%"
echo.
pause
endlocal & exit /b %SELFTEST_EXIT%
