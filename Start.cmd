@echo off
rem Plasmic EC: opens the control screen and connects to the RPi.
rem Closing this window ends the program.
call "%~dp0installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
cd /d "%~dp0pc"
"runtime\python\python.exe" app.py
pause
