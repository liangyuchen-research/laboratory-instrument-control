#!/usr/bin/env python3
"""Plasmic electronic control - burn the firmware into the pump control board ( After ”Arduino Mega 2560) ,Ask which version it is.

    .\\pc\\runtime\\python\\python.exe -B .\\mega\\tools\\flash_mega.py            burn mega\\hex\\plasmic-mega.hex
    .\\pc\\runtime\\python\\python.exe -B .\\mega\\tools\\flash_mega.py --stock    Return to independent laboratory v7 original firmware
    .\\pc\\runtime\\python\\python.exe -B .\\mega\\tools\\flash_mega.py --check    Don’t burn it, just ask which version the board is

    --port COM3   designated connection port (if not given, find it yourself: the only one on this computer Arduino Mega) 
    --yes         Don’t ask “Do you want to continue?”

do three things: 
  1. found the board (see USB identification code,No other ports will be opened) ; 
  2. Use the one included in the folder When installing avrdude is written and read back bit by bit for comparison after writing;
  3. Open the port and wait for the board to turn on, ask `?` and `G,?`.These two instructions are only queries and will not move anything.

Programming and opening the port will cause the board to restart: the pump will stop and the solenoid valve will close. Before starting, close the operating screen (Start.cmd) 
and independent laboratory v7; The same port can only be used by one program at a time.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEX = ROOT / 'mega' / 'hex'
OURS, STOCK = HEX / 'plasmic-mega.hex', HEX / 'v7-stock.hex'
AVRDUDE = ROOT / 'nano' / 'tools' / 'avrdude'
MEGA_USB_IDS = frozenset({('2341', '0042'), ('2A03', '0042')})      # the same two as pc/pumpboard.py
BAUD = 115200
UI_PORT = 8766
KV = re.compile(r'([A-Za-z_]+)=([^,]*)')

sys.path.insert(0, str(ROOT / 'pc' / 'vendor' / 'site'))            # pyserial travels with the folder


def our_tag():
    """The name of the firmware of this folder: FW_TAG of make_firmware.py ('' when it cannot be read)."""
    try:
        source = Path(__file__).with_name('make_firmware.py').read_text(encoding='utf-8')
    except OSError:
        return ''
    found = re.search(r"^FW_TAG = '(p\d+)'", source, re.M)
    return found.group(1) if found else ''


def say(text=''):
    print(text, flush=True)


def megas():
    """The Arduino Mega boards of this PC: [(device, description, serial number)]. Listing opens nothing."""
    from serial.tools import list_ports
    found = []
    for port in list_ports.comports():
        ids = ('%04X' % port.vid if port.vid is not None else '', '%04X' % port.pid if port.pid is not None else '')
        if ids in MEGA_USB_IDS:
            found.append((port.device, port.description or '', port.serial_number or ''))
    return sorted(found)


def ui_running():
    """Is the operating screen of this PC running? (It holds the port.)"""
    probe = socket.socket()
    probe.settimeout(0.5)
    try:
        return probe.connect_ex(('127.0.0.1', UI_PORT)) == 0
    finally:
        probe.close()


def burn(device, image):
    """avrdude: write, then read back and compare. True when both went well."""
    command = [str(AVRDUDE / 'avrdude.exe'), '-C', str(AVRDUDE / 'avrdude.conf'), '-p', 'atmega2560', '-c', 'wiring',
               '-P', device, '-b', str(BAUD), '-D', '-U', 'flash:w:%s:i' % image]
    done = subprocess.run(command, capture_output=True)
    text = (done.stdout + done.stderr).decode('utf-8', 'replace')
    keep = ('written', 'verified', 'error', 'not in sync', 'timeout', "can't open", 'mismatch', 'failed')
    for line in text.splitlines():               # (the progress bars are left out)
        if any(word in line.lower() for word in keep):
            say('    ' + line.strip())
    return done.returncode == 0 and 'verified' in text


def ask_board(device, wait_s=8.0):
    """Open the port (the board restarts), wait for its banner, ask `?` and `G,?`. Nothing that moves is sent.
    Returns (banner, state fields, the GSTAT line or '')."""
    import serial
    banner, fields, dose = '', {}, ''
    with serial.Serial(device, BAUD, timeout=0.2) as port:
        limit = time.time() + wait_s
        while time.time() < limit and not banner:
            line = port.readline().decode('utf-8', 'replace').strip()
            if line.startswith('READY'):
                banner = line
        for command, prefix in (('?', 'STATE,'), ('G,?', ('GSTAT,', 'ERR'))):
            port.write((command + '\n').encode('ascii'))
            limit = time.time() + 2.0
            while time.time() < limit:
                line = port.readline().decode('utf-8', 'replace').strip()
                if line.startswith(prefix):
                    if command == '?':
                        fields = dict(KV.findall(line[len('STATE,'):]))
                    else:
                        dose = line
                    break
    return banner, fields, dose


def main():
    parser = argparse.ArgumentParser(description='Burn the firmware into the pump control board (Arduino Mega 2560) ')
    parser.add_argument('--stock', action='store_true', help='Burn back to independent laboratory v7 original firmware')
    parser.add_argument('--check', action='store_true', help='Don’t burn it, just ask which version the board is')
    parser.add_argument('--port', default='', help='port (For example COM3) ; If you don’t give it, find it yourself.')
    parser.add_argument('--yes', action='store_true', help='Don’t ask “Do you want to continue?”')
    args = parser.parse_args()

    image = STOCK if args.stock else OURS
    if not args.check:
        if not image.is_file():
            say('[failed] not found %s (Execute first mega\\tools\\build_firmware.py) ' % image.relative_to(ROOT))
            return 2
        say('firmware file:%s' % image.relative_to(ROOT))
        say('SHA-256: %s' % hashlib.sha256(image.read_bytes()).hexdigest())

    boards = megas()
    if args.port:
        boards = [row for row in boards if row[0].upper() == args.port.upper()]
        if not boards:
            say('[failed] %s Not now Arduino Mega (or not connected). Nothing was done.' % args.port)
            return 2
    if len(boards) != 1:
        say('[failed] ' + ('not found Arduino Mega: Please connect the USB line.' if not boards else
                         'This computer has more than one connection Arduino Mega (%s) : Please use --port Specify.' % ', '.join(row[0] for row in boards)))
        return 2
    device, description, serial_number = boards[0]
    say('pump control board:%s (%s,serial number %s) ' % (device, description, serial_number or '—'))
    if ui_running():
        say('[failed] The operation screen is executing (it occupies this port). Please close the operation screen window and try again.')
        return 2

    if not args.check:
        say()
        say('The board will restart during programming: the pump will stop and the solenoid valve will close. The')
        if not args.yes and input('Do you want to continue? input y Press again Enter: ').strip().lower() != 'y':
            say('is not burned.')
            return 1
        say('is written and compared (approx. 20 seconds)…')
        if not burn(device, image):
            say('[failed] burning failed. The program in the board may be incomplete: please run it again.')
            return 3
        say('[ OK ] has been written and is identical bit by bit.')

    say()
    say('Wait for the board to turn on and ask which version it is...')
    try:
        banner, fields, dose = ask_board(device)
    except Exception as exc:                     # the port is held by another program, or the cable came out
        say('[failed] cannot be opened %s: %s' % (device, exc))
        return 4
    say('  boot message:%s' % (banner or ' (not received)'))
    say('  firmware: %s' % (('Plasmic %s (The weighing quantity is determined by the board itself)' % fields['fw']) if fields.get('fw') else
                        'independent laboratory v7 Original' if fields else ' (No response)'))
    if fields:
        say('  motor: A Group %s, B Group %s; solenoid valve %s; Cleaning %s' % (fields.get('sa'), fields.get('sb'), fields.get('valves'),
                                                             fields.get('cleaning')))
    if fields and fields.get('clean'):
        water, _, out = fields['clean'].partition(':')
        say('  Pump for cleaning (motor number): water inlet %s, OUT %s; motor 2~9 linked solenoid valve (0 = None):%s' % (water, out, fields.get('links')))
    if dose:
        say('  Weighing and quantitative:%s' % dose)
    # After writing, the board must name what was written: V7's own names nothing, ours names this folder's build.
    name = fields.get('fw', '') if fields else ''
    if args.check:
        good = bool(fields)
    elif args.stock:
        good = bool(fields) and name == ''
    else:
        good = bool(fields) and (name == our_tag() if our_tag() else name.startswith('p'))
    if good and fields.get('sa') == 'idle' and fields.get('sb') == 'idle' and set(fields.get('valves', '')) <= {'0'}:
        say('[ OK ] The board responded, the motors were stopped and the solenoid valves were closed.')
        return 0
    say('[Attention] The response of the board is different from expected. Please show the above lines to the maintainer.')
    return 5


if __name__ == '__main__':
    sys.exit(main())
