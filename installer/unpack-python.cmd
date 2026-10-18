@echo off
rem Unpacks the bundled Python on first use. Called by Install.cmd and the Start*.cmd files.
set "PLASMIC_PC=%~dp0..\pc"
if exist "%PLASMIC_PC%\runtime\python\python.exe" exit /b 0
if not exist "%PLASMIC_PC%\runtime\python-3.12.10-embed-amd64.zip" (
  echo Missing pc\runtime\python-3.12.10-embed-amd64.zip - please copy the whole folder again.
  pause
  exit /b 1
)
echo First run: unpacking the bundled Python, this takes a few seconds...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%PLASMIC_PC%\runtime\python-3.12.10-embed-amd64.zip' -DestinationPath '%PLASMIC_PC%\runtime\python' -Force"
if exist "%PLASMIC_PC%\runtime\python\python.exe" exit /b 0
echo Could not unpack the bundled Python. Unzip pc\runtime\python-3.12.10-embed-amd64.zip into pc\runtime\python by hand.
pause
exit /b 1
