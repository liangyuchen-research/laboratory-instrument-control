@echo off
setlocal EnableExtensions
set "EXIT_CODE=0"
chcp 65001 >nul 2>&1
title Autonomous Laboratory v6 - Control Service
cd /d "%~dp0"

echo.
echo   ================================================
echo     Autonomous Laboratory v6 - Control Service
echo   ================================================
echo.

REM =====================================================================
REM  1. Find Python 3.9+
REM     Order: manual override -> py launcher -> PATH -> common installs
REM     Everything is resolved to a full path in PYEXE.
REM =====================================================================
set "PYEXE="

REM --- manual override: put the full path to python.exe in python-path.txt
if exist "python-path.txt" (
  for /f "usebackq delims=" %%i in ("python-path.txt") do call :setpy "%%i"
)

REM --- py launcher (installed by every python.org installer)
if not defined PYEXE call :fromcmd py -3
if not defined PYEXE call :fromcmd py

REM --- on PATH
if not defined PYEXE call :fromcmd python
if not defined PYEXE call :fromcmd python3

REM --- python.org default locations
if not defined PYEXE for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :setpy "%%d\python.exe"
if not defined PYEXE for /d %%d in ("%ProgramFiles%\Python3*")                 do call :setpy "%%d\python.exe"
set "PF86=%ProgramFiles(x86)%"
if not defined PYEXE for /d %%d in ("%PF86%\Python3*")                        do call :setpy "%%d\python.exe"
if not defined PYEXE for /d %%d in ("C:\Python3*")                             do call :setpy "%%d\python.exe"

REM --- Anaconda / Miniconda
if not defined PYEXE call :setpy "%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE call :setpy "%USERPROFILE%\miniconda3\python.exe"
if not defined PYEXE call :setpy "%USERPROFILE%\AppData\Local\anaconda3\python.exe"
if not defined PYEXE call :setpy "%USERPROFILE%\AppData\Local\miniconda3\python.exe"
if not defined PYEXE call :setpy "%ProgramData%\Anaconda3\python.exe"
if not defined PYEXE call :setpy "%ProgramData%\Miniconda3\python.exe"
if not defined PYEXE call :setpy "C:\Anaconda3\python.exe"

if not defined PYEXE goto nopython

echo   [1/4] Python found:
echo         %PYEXE%
"%PYEXE%" tools\probe.py --require
if errorlevel 1 goto oldpython

REM =====================================================================
REM  2. Virtual environment
REM =====================================================================
set "VENV=%~dp0.venv\Scripts\python.exe"
if exist "%VENV%" (
  "%VENV%" tools\probe.py --require >nul 2>&1
  if errorlevel 1 (
    echo.
    echo   [2/4] Virtual environment path changed - repairing it...
    "%PYEXE%" -m venv --upgrade .venv
  )
)
if not exist "%VENV%" (
  echo.
  echo   [2/4] First run - creating virtual environment ^(about 1 minute^)...
  "%PYEXE%" -m venv .venv
  if errorlevel 1 goto venvfail
  "%VENV%" -m pip install --upgrade pip --quiet
  echo   [2/4] Installing dependencies...
  "%VENV%" -m pip install -r requirements.txt
  if errorlevel 1 goto pipfail
) else (
  echo   [2/4] Virtual environment OK
)

"%VENV%" tools\probe.py --require >nul 2>&1
if errorlevel 1 goto venvfail
"%VENV%" -c "import fastapi,serial,yaml" >nul 2>&1
if errorlevel 1 (
  echo   [2/4] Restoring missing dependencies...
  "%VENV%" -m pip install -r requirements.txt
  if errorlevel 1 goto pipfail
)

REM =====================================================================
REM  3. Generate firmware config from hardware.yaml
REM =====================================================================
echo.
echo   [3/4] Generating firmware config from config\hardware.yaml
"%VENV%" -X utf8 tools\gen_firmware_config.py
if errorlevel 1 goto fail

REM =====================================================================
REM  4. Start
REM =====================================================================
set "URL=http://127.0.0.1:8000"
for /f "usebackq delims=" %%u in (`"%VENV%" -X utf8 tools\url.py`) do set "URL=%%u"

echo.
echo   [4/4] Starting server...
echo.
echo   Console:  %URL%
echo   The browser opens automatically in a few seconds.
echo   Press Ctrl+C in THIS window to stop the service.
echo   ------------------------------------------------
echo.

start "" /min cmd /c timeout /t 4 /nobreak ^>nul ^& start %URL%
"%VENV%" -X utf8 -m backend.main
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo   Server stopped.
goto stop

REM =====================================================================
REM  Subroutines
REM =====================================================================
:fromcmd
REM  %*  = a command that might be Python. Ask it where it really lives.
if defined PYEXE goto :eof
for /f "delims=" %%i in ('%* tools\probe.py 2^>nul') do call :setpy "%%i"
goto :eof

:setpy
REM  %1 = candidate full path to python.exe
if defined PYEXE goto :eof
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof
"%~1" tools\probe.py >nul 2>&1 || goto :eof
set "PYEXE=%~1"
goto :eof

REM =====================================================================
REM  Failure messages
REM =====================================================================
:nopython
set "EXIT_CODE=1"
echo   [X] Python 3.9+ not found.
echo.
echo   Searched:
echo       py -3  /  python  /  python3   ^(on PATH^)
echo       %LOCALAPPDATA%\Programs\Python\Python3*
echo       %ProgramFiles%\Python3*
echo       C:\Python3*
echo       Anaconda3 / Miniconda3 in the usual places
echo.
echo   HOW TO FIX - pick one:
echo.
echo    A) Install Python  ^(recommended^)
echo       1. Open https://www.python.org/downloads/
echo       2. Download the Windows installer and run it
echo       3. On the FIRST screen tick  "Add python.exe to PATH"
echo       4. Finish, close this window, double-click run.bat again
echo.
echo    B) Already have Python somewhere ^(e.g. Anaconda^)
echo       1. Find the full path of python.exe
echo       2. Create a file called  python-path.txt  next to run.bat
echo       3. Put ONLY that path inside, for example:
echo          C:\Python\python.exe
echo       4. Save, then double-click run.bat again
echo.
goto stop

:oldpython
set "EXIT_CODE=1"
echo.
echo   [X] This Python is too old. Version 3.9 or newer is required.
echo       Path: %PYEXE%
echo.
echo       Install a newer one from https://www.python.org/downloads/
echo       or point python-path.txt at a newer python.exe.
echo.
goto stop

:venvfail
set "EXIT_CODE=1"
echo.
echo   [X] Could not create the virtual environment.
echo       Rename the .venv folder and run run.bat again.
echo.
goto stop

:pipfail
set "EXIT_CODE=1"
echo.
echo   [X] Could not install the dependencies ^(network or proxy issue^).
echo       Try this command in the same window to see the real error:
echo         .venv\Scripts\python -m pip install -r requirements.txt
echo.
goto stop

:fail
set "EXIT_CODE=1"
echo.
echo   [X] The step above failed. Review the diagnostic output above.
echo.

:stop
echo.
pause
endlocal & exit /b %EXIT_CODE%
