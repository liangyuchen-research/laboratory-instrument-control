@echo off
rem Production start for a touch panel: program in a minimized window, Edge in full screen.
rem Leave full screen with Alt+F4 (needs a keyboard), then close the "Plasmic EC" window.
call "%~dp0installer\unpack-python.cmd"
if errorlevel 1 exit /b 1
cd /d "%~dp0pc"
start "Plasmic EC" /min "runtime\python\python.exe" app.py --no-browser
rem Wait up to 30 s for the program before opening the screen.
powershell.exe -NoProfile -Command "$t=[DateTime]::Now.AddSeconds(30); while([DateTime]::Now -lt $t){ try { (New-Object Net.Sockets.TcpClient('127.0.0.1',8766)).Close(); exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
start "" "%EDGE%" --kiosk http://127.0.0.1:8766/ --edge-kiosk-type=fullscreen --no-first-run --disable-pinch --overscroll-history-navigation=0
