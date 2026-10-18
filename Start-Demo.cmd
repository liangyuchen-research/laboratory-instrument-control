@echo off
rem Plasmic EC practice mode: no equipment needed. A simulator runs on this PC (127.0.0.1:8767).
rem Do not run together with Start.cmd - both use the local web port 8766.
call "%~dp0installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
cd /d "%~dp0pc"
"runtime\python\python.exe" app.py --demo
pause
