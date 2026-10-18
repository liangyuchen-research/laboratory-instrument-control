@echo off
rem Flashes firmware 1.2.1 into a NEW classic Arduino Nano (ATmega328P) over USB.
rem Connect only the USB cable - the Nano must not be installed in the machine yet.
rem Double-click this file and follow the screen.
call "%~dp0installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
"%~dp0pc\runtime\python\python.exe" "%~dp0installer\flash_nano.py" %*
if errorlevel 1 pause
