#!/bin/bash
# is installed via ”one-click installation” on your computer (Install.cmd) via SSH execution; you can also do it yourself RPi execute on: 
#     bash remote_install.sh
#
# automatically determines this RPi situation: 
#   has been installed Plasmic Tool (Old HMI Can be used)  → only for additional installation TCP server,Nothing else will move
#   brand new RPi                           → complete installation, after completion 1 Automatically restart in minutes
# and change the IP add TCP server.
#
# No way Nano/HSPY sends any output or high voltage command.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$HOME/Desktop/Plasmic"
CLIENT_IP="${SSH_CLIENT:-}"
CLIENT_IP="${CLIENT_IP%% *}"

ARGS=(--user "$(id -un)")
if [ -f "$BASE/nano_lcd_v1_1/rpi_tools/plasma_job.py" ] && [ -f "$BASE/app/HSPY_Read_Status.py" ]; then
  echo "this one RPi has been installed Plasmic Tool: Installation only TCP server, verified tools, drivers and settings will not be changed."
  ARGS+=(--tcpd-only)
  FULL=0
else
  echo "this one RPi Not yet Plasmic Tools: Perform a full installation (takes a moment)."
  FULL=1
fi
if [ -n "$CLIENT_IP" ]; then
  echo "will make this computer IP ($CLIENT_IP) added to the allowed list."
  ARGS+=(--allow "$CLIENT_IP")
fi
# bash is executed while reading: first check the syntax of the main installation script, and if there is any problem, do not execute it at all.
if ! bash -n "$HERE/install_rpi.sh"; then
  echo "install_rpi.sh failed the syntax check: no installation action was performed. Please report the above message."
  exit 2
fi
if ! sudo -n true 2>/dev/null; then
  echo "Next, system management permissions are required: see [sudo] password , please enter again RPi password and press Enter (will not be displayed when input)."
fi

sudo bash "$HERE/install_rpi.sh" "${ARGS[@]}"
CODE=$?

# The one-click installation upload contains token file, delete it after use. The folder you copied will not be deleted.
case "$HERE" in
  */plasmic_ec_rpi_[0-9]*) rm -rf "$HERE" ;;
esac

if [ "$CODE" -ne 0 ]; then
  echo "The installation is not completed (end code $CODE) .Please see the last few lines of message above."
  exit "$CODE"
fi
if [ "$FULL" -eq 1 ]; then
  # end code 10 tells the installation program on the computer: the installation is successful, but you have to wait RPi Can’t connect until restarting.
  echo "Complete installation completed.RPi will be in 1 Automatically reboot after a few minutes for the driver and group settings to take effect. End by itself in"
  sudo shutdown -r +1 >/dev/null 2>&1 || echo "Unable to schedule a reboot, please perform it yourself:sudo reboot"
  exit 10
fi
exit 0
