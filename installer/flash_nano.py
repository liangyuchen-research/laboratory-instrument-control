#!/usr/bin/env python3
"""Plasmic electronic control - firmware 1.2.1 Burn in new Arduino Nano (Depend on Flash-Nano.cmd starts).

is only suitable for ”traditional models” Arduino Nano” (chip ATmega328P, 16 MHz) .do four things: 
  1. Find the burning tool avrdude,and confirm that the firmware file is not damaged;
  2. Find out Nano Which one to pick up? COM port (check by the way USB driver);
  3. burned and verified (old and new) bootloader will be tried); The linkage of
  4. from USB Read back the firmware version to confirm that the new firmware is actually running.

was burned into the folder nano\\hex\\nano_rs485_v2.pulse.hex (is the same as the one on site 1.2.1 version).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NANO = ROOT / 'nano'
HEX = NANO / 'hex' / 'nano_rs485_v2.pulse.hex'
# SHA-256 of the 1.2.1 build that is running on site (from the 2026-09-29 release notes).
HEX_SHA256 = 'cdb4d9d7137f451d00f1780902e2cf9fb52cd85369290fb5fce9db686e0c24ad'
BUNDLED = NANO / 'tools' / 'avrdude'
DRIVER = NANO / 'drivers' / 'CH341SER.EXE'
# New Nanos use 115200 (Optiboot); many compatible boards still ship the old bootloader at 57600.
BAUD_RATES = (115200, 57600)
USB_CHIPS = {'1A86': 'CH340 (For most compatible boards USB chip) ', '0403': 'FTDI (Original factory Nano Used USB chip) ',
             '2341': 'Arduino', '2A03': 'Arduino', '10C4': 'CP210x', '067B': 'PL2303'}
sys.path.insert(0, str(ROOT / 'rpi' / 'rpi_tools'))  # the field-verified Nano protocol decoder

TOTAL = 4
ARGS = None


def say(text=''):
    print(text, flush=True)


def heading(number, text):
    say()
    say(f'[{number}/{TOTAL}] {text}')
    say('─' * 62)


def ok(text):
    say('  [ OK ] ' + text)


def note(text):
    say('  [Attention] ' + text)


def fail(text):
    say('  [failed] ' + text)


def ask(prompt, default=''):
    if ARGS.yes:
        return default
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


# ---------------------------------------------------------------- 1 Tools and Firmware Files

def find_avrdude():
    """(avrdude.exe, avrdude.conf or None), preferring the copy that travels with this folder."""
    candidates = [(BUNDLED / 'avrdude.exe', BUNDLED / 'avrdude.conf')]
    local = os.environ.get('LOCALAPPDATA')
    if local:  # Arduino IDE 2.x keeps its tools here
        tools = Path(local) / 'Arduino15' / 'packages' / 'arduino' / 'tools' / 'avrdude'
        if tools.is_dir():
            for version in sorted(tools.iterdir(), reverse=True):
                candidates.append((version / 'bin' / 'avrdude.exe', version / 'etc' / 'avrdude.conf'))
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):  # Arduino IDE 1.x
        if base:
            avr = Path(base) / 'Arduino' / 'hardware' / 'tools' / 'avr'
            candidates.append((avr / 'bin' / 'avrdude.exe', avr / 'etc' / 'avrdude.conf'))
    for exe, conf in candidates:
        if exe.is_file():
            return exe, conf if conf.is_file() else None
    on_path = shutil.which('avrdude')
    if on_path:
        conf = Path(on_path).with_name('avrdude.conf')
        return Path(on_path), conf if conf.is_file() else None
    return None


def firmware_problem():
    """'' when the firmware file is the verified 1.2.1 build, otherwise what is wrong."""
    if not HEX.is_file():
        return f'Firmware file not found {HEX.relative_to(ROOT)}'
    digest = hashlib.sha256(HEX.read_bytes()).hexdigest()
    if digest != HEX_SHA256:
        return f'Firmware file {HEX.name} content and verified 1.2.1 version is different (the file may be damaged or altered)'
    return ''


def step_tools():
    heading(1, 'Check the burning tool and firmware file')
    problem = firmware_problem()
    if problem:
        fail(problem + '.Please copy the entire folder again.')
        return None
    ok(f'Firmware file {HEX.name} is verified 1.2.1 version')
    found = find_avrdude()
    if not found:
        fail('The burning tool cannot be found avrdude.')
        say(f'         It should be in the folder {BUNDLED.relative_to(ROOT)}\\avrdude.exe.')
        say('         If not: Install Arduino IDE (It comes with avrdude) , or use the instructions ”References › use Arduino IDE Burning” method.')
        return None
    exe, conf = found
    source = 'included in folder' if BUNDLED in exe.parents else 'Arduino IDE included' if 'Arduino' in str(exe) else 'system'
    ok(f'Burning tool avrdude: {source} ({exe}) ')
    return found


# ---------------------------------------------------------------- 2 try to find Nano

def list_serial_devices():
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                               str(HERE / 'serial-ports.ps1')], capture_output=True, timeout=60)
        return json.loads(done.stdout.decode('utf-8-sig')) if done.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def classify(devices):
    """(usable ports, devices whose driver is missing) among the USB chips Nano boards use."""
    usable = [row for row in devices if row.get('port') and row.get('error') == 0 and row.get('vid') in USB_CHIPS]
    broken = [row for row in devices if row.get('error') != 0 and row.get('vid') in USB_CHIPS]
    return usable, broken


def describe(row):
    return f'{row["port"] or " (None COM port)"} {row["name"]} [{USB_CHIPS.get(row["vid"], row["vid"])}]'


def step_find_port():
    heading(2, 'looking for Nano Which one to pick up? COM port')
    if ARGS.port:
        ok(f'Use the specified {ARGS.port}')
        return ARGS.port
    while True:
        usable, broken = classify(list_serial_devices())
        if len(usable) == 1:
            ok('find one: ' + describe(usable[0]))
            return usable[0]['port']
        if len(usable) > 1:
            say('  Found several USB sequence device: ')
            for index, row in enumerate(usable, 1):
                say(f'    [{index}] {describe(row)}')
            say('  Not sure which one: Nano of USB Unplug, press Enter Rearrange, the one that disappears is it; insert it back again.')
            answer = ask('  Enter the number (press directly Enter=rearranged):', '')
            if answer.isdigit() and 1 <= int(answer) <= len(usable):
                return usable[int(answer) - 1]['port']
            if ARGS.yes:
                return ''
            continue
        if broken:
            fail('Computer can see Nano of USB chip, but no driver:')
            for row in broken:
                say('           ' + describe(row))
            if any(row['vid'] == '1A86' for row in broken):
                if DRIVER.is_file():
                    say(f'         Please double-click the {DRIVER.relative_to(ROOT)},according to INSTALL installation CH340 driver (requires system administrator rights),')
                    say('         After installing it, put USB Unplug and plug back in.')
                else:
                    say('         Please install CH340 driver (chip original factory WCH of CH341SER.EXE) ,After installing it, put USB Unplug and plug back in.')
            else:
                say('         Please connect the computer to the Internet.Windows will automatically install the driver; or download it from the chip’s original website.')
        else:
            fail('not found Nano.')
            say('         Please check:')
            say('           • Nano use USB cable connected to this computer? The power light on the board (PWR/ON) Is it bright?')
            say('           • Change one USB cable try: Some cables can only charge but cannot transfer data.')
            say('           • Change another one USB hole, plug directly into the computer, do not go through the hub.')
        if ARGS.yes:
            return ''
        if ask('  After processing, press Enter Search again (enter q left):', '').lower() == 'q':
            return ''


# ---------------------------------------------------------------- 3 Burn

def avrdude_command(exe, conf, port, baud):
    """The same command the 2026-09-29 install notes give, with the file name relative to its folder."""
    command = [str(exe)]
    if conf:
        command += ['-C', str(conf)]
    command += ['-p', 'atmega328p', '-c', 'arduino', '-P', port, '-b', str(baud), '-D']
    if BUNDLED in Path(exe).parents:
        # avrdude 8 waits about 5 s per connection attempt and makes 10 by default; three are
        # enough to tell that this speed is wrong. (Older avrdude builds do not know this option.)
        command += ['-x', 'attempts=3']
    return command + ['-U', f'flash:w:{HEX.name}:i']


def run_avrdude(command):
    """Runs avrdude showing its output; returns (exit code, all output)."""
    lines = []
    process = subprocess.Popen(command, cwd=str(HEX.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding=locale.getpreferredencoding(False), errors='replace')
    with process.stdout:
        for line in process.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                say('      ' + line)
    return process.wait(), '\n'.join(lines)


def wrong_chip(output):
    return 'expected signature' in output.lower()


def port_unavailable(output):
    # avrdude also ends with "unable to open port" when the bootloader never answered, so only
    # the operating system's "cannot open port" line means the port itself could not be opened.
    text = output.lower()
    return 'cannot open port' in text or "can't open device" in text


def explain_failure(output):
    """Plain-language reasons for the usual avrdude failures."""
    text = output.lower()
    hints = []
    if wrong_chip(output):
        hints.append('The chip of this board is not ATmega328P.This firmware can only be used on ”traditional models” Arduino Nano (ATmega328P) ”; '
                     'Nano Every, Nano 33, Nano ESP32 etc. cannot be used.')
    if port_unavailable(output):
        hints.append('this COM Port cannot be opened: it may be occupied by other programs (turn off Arduino IDE ’s serial port monitoring window or other serial port program), Go through the check list of'
                     'or USB Just been unplugged.Bundle USB Unplug and plug it back in and execute again.')
    elif 'not in sync' in text or 'not responding' in text:
        hints.append('Nano of bootloader No response (tried both rates). Common reasons: wrong selection COM port;'
                     'Nano of D0/D1 Next RS485 module (unplug those two wires first, then reconnect them after burning);USB line contact is poor.')
        hints.append('Very few boards leave the factory without bootloader,Then you need ISP writer, or change to a different board.')
    return hints or ['Please put the above avrdude and compare it with the instructions The firmware of 1”’s ”when things don’t go well”.']


def step_flash(exe, conf, port):
    heading(3, f'burning ({port}) ')
    say('  The firmware will overwrite this Nano . When burning Nano of D13 will flash, so:')
    say('  ▌ Nano Now we should just continue USB line has not been installed on the machine yet.')
    say('  ▌ If it has been installed on the machine: Disconnect first 24 V drives power supply and high voltage load.')
    if ask('  After confirming the above situation, enter y Press again Enter Start burning:', 'y' if ARGS.yes else 'n').lower() != 'y':
        note('is not burned.')
        return False
    output = ''
    for baud in BAUD_RATES:
        say()
        say(f'  by {baud} speed connection bootloader …')
        code, output = run_avrdude(avrdude_command(exe, conf, port, baud))
        if code == 0:
            ok(f'burning completed,avrdude The written content has been read back and compared (rate {baud}) ')
            return True
        if wrong_chip(output) or port_unavailable(output):
            break  # another speed will not help
        if baud != BAUD_RATES[-1]:
            note(f'{baud} No response, try the old model instead bootloader (many compatible boards are of this type).')
    fail('was not burned successfully.')
    for hint in explain_failure(output):
        say('         • ' + hint)
    return False


# ---------------------------------------------------------------- 4 Read back version

def decode_reply(reply):
    """Status dict from the 85-byte Modbus reply, using the field-verified decoder."""
    from nano_verify import crc16, decode_status
    if len(reply) != 85 or reply[:3] != b'\x02\x03\x50' or crc16(reply):
        raise ValueError('The reply is incomplete or the check code is incorrect.')
    return decode_status(list(struct.unpack('>40H', reply[3:-2])))


def query_status(port):
    """Ask the running firmware for its status over USB (the same request the RPi sends over RS485)."""
    from nano_verify import frame
    request = frame(struct.pack('>BBHH', 2, 3, 0, 40))
    done = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                           str(HERE / 'nano-query.ps1'), '-Port', port, '-Hex', request.hex()],
                          capture_output=True, timeout=30)
    if done.returncode != 0:
        raise OSError(done.stderr.decode('utf-8', 'replace').strip()[-200:] or 'Unable to open serial port')
    return decode_reply(bytes.fromhex(done.stdout.decode('ascii', 'replace').strip()))


def step_read_back(port):
    heading(4, 'from USB Read back the firmware version')
    time.sleep(2)
    try:
        status = query_status(port)
    except Exception as exc:  # this check is a bonus; flashing was already verified by avrdude
        note(f'None from USB Read the reply from the firmware ({exc}) .')
        say('         This does not mean that the programming failed: Previous step avrdude The written content has been compared one by one.')
        say('         Most common reasons:Nano has been connected to the circuit of the machine.RS485 module and USB shared Nano of D0/D1 two legs,')
        say('         firmware starts executing,RS485 module will occupy this line, and the computer will never It will end if it fails to start USB I can’t get there if I ask. Nano (burning is not affected).')
        say('         How to confirm that the firmware is running:Nano of LCD Displayed when powering on NANO2 FW1.2.1 PULSE,The first line after that is POWER ?*; ')
        say('         RPi After installation, click ” There are some inNano Pulse Controller” will display 1.2.1.')
        return
    pin = 'HIGH' if status['output_pin_sample'] or status['output_command_high'] else 'LOW'
    if status['firmware'] == '1.2.1' and status['state'] == 'IDLE' and status['fault'] == 'NONE' and pin == 'LOW':
        ok(f'Nano Report: Firmware {status["firmware"]}, state {status["state"]}, No fault, pulse output {pin}')
    else:
        note(f'Nano Report: Firmware {status["firmware"]}, state {status["state"]}, fault {status["fault"]}, pulse output {pin}')
        say('         The firmware responds, but the status is not as expected. 1.2.1/IDLE/NONE/LOW; Please refer to the ”Steps” before reinstalling the machine. For functions in 1”.')


def main():
    global ARGS
    parser = argparse.ArgumentParser(description='Bundle Plasmic firmware 1.2.1 Burn in new Arduino Nano')
    parser.add_argument('--port', default='', help='Directly specify COM port, for example COM5')
    parser.add_argument('--yes', action='store_true', help='No questions (unattended; will be burned directly)')
    ARGS = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    say('═' * 62)
    say('  Plasmic electronic control - burning Arduino Nano firmware 1.2.1')
    say('═' * 62)
    say('  Applicable: traditional model Arduino Nano (printed on chip ATMEGA328P) .')
    say('  Method: use USB The cable handle ”has not been installed on the machine yet” Nano Connect to this computer, and then follow the picture.')
    ask('  according to Enter Start (if you want to cancel, just close this window)…')
    done = False
    tools = step_tools()
    if tools:
        port = step_find_port()
        if port and step_flash(tools[0], tools[1], port):
            done = True
            step_read_back(port)
    say()
    say('═' * 62)
    if done:
        say('  burning completed. Next:')
        say('   1. unplug USB.The machine is powered off (including 24 V and high voltage), put Nano Put it back in the same way as the original one.')
        say('   2. Machine power on:LCD Displayed when powering on NANO2 FW1.2.1 PULSE,The first line after that is POWER ?*, The other fields are --.')
        say('      This is normal: ”?”=RPi has not informed the power status yet Nano; “--”=There are no synchronization parameters yet.')
        say('   3. RPi Installed (Install.cmd) and turn on“Plasmic Electronic Control”, the program will automatically synchronize once.')
        say('      LCD will display the parameters and POWER OFF*; Left side of screen Nano The firmware should be 1.2.1.')
        say('   4. changed Nano,Please explain.html”Checklist from scratch. The pump path of')
    else:
        say('  has not been burned yet. After troubleshooting the above problems, double-click again Flash-Nano.cmd is enough.')
    say('  Detailed instructions: docs\\detailed-guide.html, Step 1: Flash a new Arduino Nano.')
    say('═' * 62)
    ask('  according to Enter End…')
    return 0 if done else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\n  has been cancelled.')
        raise SystemExit(1)
