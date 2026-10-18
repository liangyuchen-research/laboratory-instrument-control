@echo off
rem Flashes the pump board (Arduino Mega 2560) with mega\hex\plasmic-mega.hex, verifies it,
rem and asks the board which firmware it runs. Close the operating screen and V7 first.
rem Back to V7's own firmware: run "Flash-Mega.cmd --stock" in a command window.
call "%~dp0..\installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
"%~dp0..\pc\runtime\python\python.exe" -B "%~dp0tools\flash_mega.py" %*
pause
