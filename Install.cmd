@echo off
rem One-click install. Double-click this file.
rem It prepares this PC, installs the TCP server on the RPi over the network cable,
rem fills in the connection settings and tests the connection.
rem It never sends an output or high-voltage command to the equipment.
call "%~dp0installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
"%~dp0pc\runtime\python\python.exe" "%~dp0installer\install.py" %*
if errorlevel 1 pause
