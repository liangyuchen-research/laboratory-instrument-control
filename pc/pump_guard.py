#!/usr/bin/env python3
"""Plasmic Electronic Control - Guard program for the pump control panel (only in real mode by pumpboard.py starts).

When the main program is forced to end (job manager, crash), the operation without an end point will not stop by itself:Arduino Mega The firmware does not have
Watchdog. The operation and rationing with time is that the board counts until it stops, without the need for guards; the only thing that has no end point is ”weighing and rationing” (the computer looks at the scale,
tells the pump to keep turning). This little program listens to the main program speaking from standard input. The main program disappeared without saying goodbye, and with this operation at that time, it
Re-open that port (enabling it will cause the board to restart) and send X (Stop all). Otherwise it doesn’t touch the port at all.

    python -B pump_guard.py --vid 2341 --pid 0042 --serial <serial number> --log <data>\\logs\\guard.log

Each line read from standard input:
    port COMn   The main program is connected to this port All pumps must be stopped before
    arm         Started an endless operation (weighing and quantification), or it is uncertain whether this operation will stop.
    disarm      No more
    bye         The main program ends normally: do nothing

Only load included pyserial (pc/vendor/site) .Set PLASMIC_NO_SERIAL=1 ends directly.
Only use simulated ones for testing serial module call run(),This file will never be started as a program.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

MEGA_USB_IDS = frozenset({('2341', '0042'), ('2A03', '0042')})
BAUD = 115200
SETTLE_S = 0.3           # let Windows notice that the dead program's handle is gone
OPEN_RETRY_S = 10.0      # how long to keep trying while that handle is being released
OPEN_PAUSE_S = 0.5
BOOT_S = 2.5             # opening restarts the board; it reads nothing while it boots
REPLY_S = 3.0
READ_TIMEOUT_S = 0.2

# Kept as module attributes so that the tests can run on a clock of their own.
_sleep = time.sleep
_now = time.monotonic


def _log(path, text):
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a', encoding='utf-8', newline='\n') as out:
            out.write('%s %s\n' % (datetime.now().astimezone().isoformat(timespec='seconds'), text))
    except OSError:
        pass


def _hex4(value):
    if value is None or value == '':
        return ''
    if isinstance(value, int):
        return '%04X' % value
    return str(value).strip().upper()


def _pyserial():
    site = Path(__file__).resolve().parent / 'vendor' / 'site'
    if str(site) not in sys.path:
        sys.path.insert(0, str(site))
    import serial
    from serial.tools import list_ports
    return serial, list_ports


def _same_board(row, vid, pid, serial_number):
    """Is this port still the Arduino Mega the program was talking to?"""
    found = (_hex4(getattr(row, 'vid', None)), _hex4(getattr(row, 'pid', None)))
    if found not in MEGA_USB_IDS or found != (vid, pid):
        return False
    return not serial_number or str(getattr(row, 'serial_number', None) or '') == serial_number


def stop_pumps(port, vid, pid, serial_number, log_path):
    """The program died while a pump was running: reopen the port and send X. Returns an exit code."""
    _sleep(SETTLE_S)
    serial, list_ports = _pyserial()
    row = next((item for item in list_ports.comports() if getattr(item, 'device', None) == port), None) if port else None
    if row is None or not _same_board(row, vid, pid, serial_number):
        _log(log_path, 'the program ended while a pump was running, but %s is not the pump board any more: not opened'
             % (port or '(no port)'))
        return 2
    limit = _now() + OPEN_RETRY_S
    while True:
        try:
            handle = serial.Serial(port, BAUD, timeout=READ_TIMEOUT_S)
            break
        except Exception as exc:
            if _now() >= limit:
                _log(log_path, 'the program ended while a pump was running; %s could not be opened for %g s (%s): X NOT sent'
                     % (port, OPEN_RETRY_S, exc))
                return 3
            _sleep(OPEN_PAUSE_S)
    confirmed = False
    try:
        _sleep(BOOT_S)
        handle.write(b'X\n')
        handle.flush()
        limit = _now() + REPLY_S
        while _now() < limit:
            line = handle.readline().decode('utf-8', errors='replace').strip()
            if line.startswith('OK,all_stopped'):
                confirmed = True
                break
    except Exception as exc:
        _log(log_path, 'the program ended while a pump was running; %s was opened, sending X failed (%s)' % (port, exc))
        return 4
    finally:
        try:
            handle.close()
        except Exception:
            pass
    _log(log_path, 'the program ended while a pump was running; %s was opened and X was sent: %s'
         % (port, 'OK,all_stopped' if confirmed else 'no answer within %g s' % REPLY_S))
    return 0 if confirmed else 5


def run(argv, stdin, environ=None):
    """Listen until the program says bye or disappears. Returns the exit code."""
    parser = argparse.ArgumentParser(description='Plasmic pump board guard')
    parser.add_argument('--vid', default='')
    parser.add_argument('--pid', default='')
    parser.add_argument('--serial', default='')
    parser.add_argument('--log', default='')
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if environ.get('PLASMIC_NO_SERIAL') == '1':
        _log(args.log, 'PLASMIC_NO_SERIAL is set: the guard does nothing')
        return 0
    port, armed = '', False
    for line in stdin:                   # ends when the program closes the pipe, or dies
        if isinstance(line, bytes):
            line = line.decode('ascii', errors='replace')
        word, _, rest = line.strip().partition(' ')
        if word == 'bye':
            return 0
        if word == 'port':
            port = rest.strip()
        elif word == 'arm':
            armed = True
        elif word == 'disarm':
            armed = False
    if not armed:
        return 0                         # nothing was running without an end: the port is not touched
    return stop_pumps(port, args.vid.strip().upper(), args.pid.strip().upper(), args.serial.strip(), args.log)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.exit(run(sys.argv[1:], sys.stdin))
