#!/bin/bash
# Plasmic Electronic control — RPi installation script (repeatable)
#
#   Brand new or refilled RPi:      sudo bash install_rpi.sh
#   RPi It can be used in the first place HMI:  sudo bash install_rpi.sh --tcpd-only
#
# Generally, you do not need to perform it yourself: ”One-click installation” on your computer ( The three programs onInstall.cmd) will send this folder to RPi,
# through again remote_install.sh automatically selects one of the above methods for execution.
#
# --tcpd-only   Only add or update TCP server (tcpd folder＋Boot service) ,Do not touch what has been verified in the field
#               tools, drivers and udev setting; no need to reboot.
# --allow IP    put this IP add TCP Server’s allow list (repeatable).
# --user Name   RPi ’s username (default pi) .
# tcpd/token.txt exists,TCP Server’s token will be set to the value in the file (for one-click installation).
#
# This script will not Nano/HSPY will not modify the network by sending any output or high-voltage commands. IP.
# Always back up existing files before overwriting them. $BASE/backup/install-<time>/.
set -euo pipefail

TCPD_ONLY=0
ALLOW_IPS=()
PI_USER="${PI_USER:-pi}"
while [ $# -gt 0 ]; do
  case "$1" in
    --tcpd-only) TCPD_ONLY=1 ;;
    --allow) shift; ALLOW_IPS+=("${1:-}") ;;
    --user) shift; PI_USER="${1:-pi}" ;;
    *) echo "Unrecognized parameters:$1"; exit 1 ;;
  esac
  if [ $# -gt 0 ]; then shift; fi
done
BASE="/home/${PI_USER}/Desktop/Plasmic"
APP_DIR="$BASE/app"
TOOLS_DIR="$BASE/nano_lcd_v1_1/rpi_tools"
LOG_DIR="$BASE/logs/hmi-captures"
TCPD_DIR="$BASE/tcpd"
PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BASE/backup/install-$STAMP"

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf '  [Attention] %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Please use sudo execution:sudo bash install_rpi.sh"; exit 1
fi
if ! id "$PI_USER" >/dev/null 2>&1; then
  echo "User not found $PI_USER (Available PI_USER=Name sudo -E bash install_rpi.sh Specify) "; exit 1
fi

mkdir -p "$BACKUP"

# If the target already exists and the content is different, back it up first and then overwrite it.
install_file() {  # $1=source $2=Target $3=permissions
  local src="$1" dst="$2" mode="$3"
  if [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
    mkdir -p "$BACKUP$(dirname "$dst")"
    cp -a "$dst" "$BACKUP$dst"
    echo "  Backup $dst"
  fi
  install -D -m "$mode" "$src" "$dst"
  echo "  installation $dst"
}

if [ "$TCPD_ONLY" -eq 0 ]; then
  if [ "$(dpkg --print-architecture)" != "armhf" ]; then
    warn "at present userspace architecture is $(dpkg --print-architecture); spectrometer SDK lib_linux_ARM_2.7.11.so for 32 Bit armhf,Need to use 32-bit Raspberry Pi OS."
  fi

  say "1/11 examine Python Kit"
  if python3 -c 'import serial' 2>/dev/null; then
    echo "  python3-serial Installed"
  else
    echo "  installation python3-serial (requires Internet connection)"
    apt-get update && apt-get install -y python3-serial || warn "cannot be installed python3-serial,Please connect to the Internet and execute:sudo apt install python3-serial"
  fi

  say "2/11 enable I2C (UNO220 GPIO need) "
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_i2c 0 && echo "  I2C has been enabled (the first time it is enabled, it will take effect after restarting)"
  else
    warn "not found raspi-config,Please enable it manually I2C"
  fi

  say "3/11 installation UNO220 drive"
  for deb in uno220gpio_0.1-3_armhf.deb uno220uart_0.1-3_armhf.deb; do
    name="${deb%%_*}"
    if dpkg -s "$name" >/dev/null 2>&1; then
      echo "  $name has been installed, skip"
    else
      dpkg --install "$PKG/drivers/$deb"
    fi
  done

  say "4/11 Create folder"
  install -d -o "$PI_USER" -g "$PI_USER" "$APP_DIR" "$TOOLS_DIR" "$LOG_DIR"

  say "5/11 installation RPi program (version that has been verified on site, the content has not been modified)"
  for f in HSPY_Protocol.py HSPY_Read_Status.py lib_linux_ARM_2.7.11.so; do
    install_file "$PKG/app/$f" "$APP_DIR/$f" 644
  done
  if [ -e "$APP_DIR/parameter.json" ]; then
    echo "  Keep existing $APP_DIR/parameter.json (does not cover on-site parameters)"
  else
    install -m 644 "$PKG/app/parameter.json.example" "$APP_DIR/parameter.json"
    echo "  Create a preset parameter.json (Voltage=0,needs to set the voltage on the screen before it can be discharged)"
  fi
  for f in plasma_job.py nano_verify.py pulse_prepare.py hardware_verify.py; do
    install_file "$PKG/rpi_tools/$f" "$TOOLS_DIR/$f" 644
  done
  chown -R "$PI_USER:$PI_USER" "$BASE"

  say "6/11 udev rule (/dev/RS485, Spectrometer permissions) "
  install_file "$PKG/system/99-usb-serial.rules" /etc/udev/rules.d/99-usb-serial.rules 644
  install_file "$PKG/system/70-plasmic-spectrometer.rules" /etc/udev/rules.d/70-plasmic-spectrometer.rules 644
  udevadm control --reload-rules
  udevadm trigger

  say "7/11 When booting Nano Trigger pin (UNO220 pin 3) set to LOW"
  install_file "$PKG/system/uno220-trigger-low.py" /usr/local/sbin/uno220-trigger-low.py 755
  install_file "$PKG/system/plasmic-trigger-low.service" /etc/systemd/system/plasmic-trigger-low.service 644
  systemctl daemon-reload
  systemctl enable plasmic-trigger-low.service
  systemctl restart plasmic-trigger-low.service || warn "trigger-low The service was not successful this time (the driver was installed for the first time)/I2C is normal, please check again after restarting the computer) When"

  if [ -f "/home/$PI_USER/rtc_driver.py" ]; then
    # What is written in the service file is /home/pi; The login name is not pi is replaced by the actual home directory (is pi ).
    TMP_RTC="$(mktemp)"
    sed -e "s|/home/pi/|/home/$PI_USER/|g" "$PKG/system/rtc-rx8010.service" > "$TMP_RTC"
    install_file "$TMP_RTC" /etc/systemd/system/rtc-rx8010.service 644
    rm -f "$TMP_RTC"
    systemctl daemon-reload
    systemctl enable rtc-rx8010.service
  else
    warn "not found /home/$PI_USER/rtc_driver.py (UNO220 RTC driver, not included in this installation package), skip RTC Serve"
  fi

  say "8/11 Disable old version services and serial port login (to avoid occupation) ”"
  systemctl mask rs232_executor.service rs232-executor.service >/dev/null 2>&1 || true
  systemctl stop serial-getty@ttyS0.service >/dev/null 2>&1 || true
  systemctl mask serial-getty@ttyS0.service >/dev/null 2>&1 || true
  CMDLINE=/boot/firmware/cmdline.txt
  [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
  if [ -f "$CMDLINE" ] && grep -q 'console=serial0,[0-9]*' "$CMDLINE"; then
    mkdir -p "$BACKUP$(dirname "$CMDLINE")"
    cp -a "$CMDLINE" "$BACKUP$CMDLINE"
    sed -i 's/console=serial0,[0-9]* \{0,1\}//' "$CMDLINE"
    echo "  Already from $CMDLINE Remove serial0 console (has been backed up and will take effect after restarting)"
  fi

  say "9/11 User Group and Time Zone"
  for g in dialout plugdev gpio i2c; do
    if getent group "$g" >/dev/null; then usermod -aG "$g" "$PI_USER"; fi
  done
  echo "  $PI_USER group: $(id -nG "$PI_USER")"
  timedatectl set-timezone Asia/Taipei || true
else
  say "only for additional installation TCP server (--tcpd-only) : Do not change tools, drivers and udev"
  if [ ! -f "$TOOLS_DIR/plasma_job.py" ] || [ ! -f "$APP_DIR/HSPY_Read_Status.py" ]; then
    echo "not found $TOOLS_DIR/plasma_job.py: this one RPi has not been installed yet Plasmic tool, please use instead sudo bash install_rpi.sh (No parameters) "; exit 1
  fi
  # measurement program plasma_measure.py directly uses the functions and file conventions in the following tools (instead of just calling them).
  # RPi is different from the installation package, the measurement may go wrong midway, so we stop here and install nothing.
  for f in plasma_job.py nano_verify.py pulse_prepare.py hardware_verify.py; do
    if ! cmp -s "$PKG/rpi_tools/$f" "$TOOLS_DIR/$f"; then
      echo "RPi on $f is different from the version of this installation package: nothing is installed."
      echo "New measurement program (plasma_measure.py) will directly use the functions in it. Different versions may cause errors during measurement."
      echo "To use this version, please do a complete installation (it will be backed up first, and then replaced with the tools in the installation package. After completion RPi To restart):"
      echo "  1. First use the physical switch to turn it off 24 V Drive power and high voltage."
      echo "  2. Photo Instructions ”Reference Materials › Manual installation RPi end”, in RPi execute on: sudo bash install_rpi.sh --user $PI_USER (Not added --tcpd-only) "
      exit 3
    fi
  done
fi

say "10/11 installation TCP server ($TCPD_DIR) "
install -d -o "$PI_USER" -g "$PI_USER" "$TCPD_DIR" "$LOG_DIR"
install_file "$PKG/tcpd/plasmic_tcpd.py" "$TCPD_DIR/plasmic_tcpd.py" 644
install_file "$PKG/tcpd/read_status.py" "$TCPD_DIR/read_status.py" 644
# Measurement program: use rpi_tools do not replace or modify them.
install_file "$PKG/tcpd/plasma_measure.py" "$TCPD_DIR/plasma_measure.py" 644
# 1.3.0 once installed one plasma_quick.py,already by plasma_measure.py Replacement: Moved after backup.
if [ -e "$TCPD_DIR/plasma_quick.py" ]; then
  mkdir -p "$BACKUP$TCPD_DIR"
  mv "$TCPD_DIR/plasma_quick.py" "$BACKUP$TCPD_DIR/plasma_quick.py"
  echo "  Remove the old one $TCPD_DIR/plasma_quick.py (has been backed up)"
fi
TOKEN_FILE="$PKG/tcpd/token.txt"
if [ -e "$TCPD_DIR/config.json" ]; then
  mkdir -p "$BACKUP$TCPD_DIR"
  cp -a "$TCPD_DIR/config.json" "$BACKUP$TCPD_DIR/config.json"
fi
# Create or update configuration file: The first installation generates a random token; provides one-click installation token.txt ;
# --allow of IP added to the allowed list. Other fields (such as those you have modified allow, watchdog_s) remain unchanged.
python3 - "$PKG/tcpd/config.example.json" "$TCPD_DIR/config.json" "$BASE" "$TOKEN_FILE" ${ALLOW_IPS[@]+"${ALLOW_IPS[@]}"} <<'PY'
import json, os, re, secrets, sys
source, target, base, token_file = sys.argv[1:5]
created = not os.path.exists(target)
with open(source if created else target, encoding='utf-8-sig') as stream:
    config = json.load(stream)
changes = []
if created:
    config['app_dir'] = base + '/app'
    config['tools_dir'] = base + '/nano_lcd_v1_1/rpi_tools'
    config['output_dir'] = base + '/logs/hmi-captures'
    config['token'] = secrets.token_hex(8)
    changes.append('Create profile')
if os.path.exists(token_file):
    with open(token_file, encoding='utf-8-sig') as stream:
        token = stream.read().strip()
    if not re.fullmatch(r'[0-9a-f]{16,64}', token):
        sys.exit('tcpd/token.txt is not legal token')
    if config.get('token') != token:
        config['token'] = token
        changes.append('token is changed to the value provided by the computer installation program')
for address in sys.argv[5:]:
    if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', address) and address not in config['allow']:
        config['allow'].append(address)
        changes.append('Allow list to join ' + address)
if changes:
    with open(target, 'w', encoding='utf-8') as out:
        json.dump(config, out, ensure_ascii=False, indent=2)
        out.write('\n')
print('  ' + ('; '.join(changes) if changes else 'Keep existing profile, no changes'))
PY
chmod 600 "$TCPD_DIR/config.json"
chown -R "$PI_USER:$PI_USER" "$TCPD_DIR"

say "11/11 starts automatically at boot TCP server (plasmic-tcpd.service) "
UNIT=/etc/systemd/system/plasmic-tcpd.service
TMP_UNIT="$(mktemp)"
sed -e "s|@USER@|$PI_USER|g" -e "s|@BASE@|$BASE|g" "$PKG/system/plasmic-tcpd.service" > "$TMP_UNIT"
install_file "$TMP_UNIT" "$UNIT" 644
rm -f "$TMP_UNIT"
systemctl daemon-reload
systemctl enable plasmic-tcpd.service
systemctl restart plasmic-tcpd.service || warn "TCP The server did not start successfully this time; please see journalctl -u plasmic-tcpd -n 30"

rmdir "$BACKUP" 2>/dev/null && echo "  (There are no files that need to be backed up this time)" || echo "  Backup location: $BACKUP"

say "installation completed, check the results"
sleep 2
printf '  /dev/RS485           : %s\n' "$(readlink -f /dev/RS485 2>/dev/null || echo 'has not appeared yet (confirm RS485 USB is plugged in)')"
printf '  trigger-low Serve     : %s\n' "$(systemctl is-active plasmic-trigger-low.service 2>/dev/null || true)"
printf '  python3 serial       : %s\n' "$(python3 -c 'import serial;print(serial.__version__)' 2>/dev/null || echo Lack)"
printf '  spectrometer USB 0638:0aac : %s\n' "$(lsusb -d 0638:0aac >/dev/null 2>&1 && echo Detected || echo not detected)"
printf '  TCP server           : %s\n' "$(systemctl is-active plasmic-tcpd.service 2>/dev/null || true)"
printf '  TCP port               : %s\n' "$(python3 -c "import json;print(json.load(open('$TCPD_DIR/config.json'))['port'])")"
printf '  Computer that allows connection IP    : %s\n' "$(python3 -c "import json;print(', '.join(json.load(open('$TCPD_DIR/config.json'))['allow']))")"
echo
if [ -e "$TOKEN_FILE" ]; then
  echo "  token has been set by the installation program on the computer. The computer will fill it in automatically and does not need to be copied."
else
  echo "  ┌─────────────────────────────────────────────────────────────┐"
  printf '  │ token (Please copy it and fill it in the ”Connection Settings” on the computer screen): %s\n' "$(python3 -c "import json;print(json.load(open('$TCPD_DIR/config.json'))['token'])")"
  echo "  └─────────────────────────────────────────────────────────────┘"
  echo "  To watch again later: Betweencat $TCPD_DIR/config.json"
fi
echo
if [ "$TCPD_ONLY" -eq 0 ]; then
  echo "Complete installation requires restarting to take effect (sudo reboot) ."
else
  echo "RPi end completed."
fi
