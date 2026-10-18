@echo off
rem Sets the 6-8 digit maintenance PIN. The maintenance pages stay closed until a PIN is set.
rem Run this again to replace a forgotten PIN.
call "%~dp0installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
cd /d "%~dp0pc"
"runtime\python\python.exe" app.py --set-pin
pause
