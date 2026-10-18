#!/usr/bin/env python3
"""Plasmic Electronic control — PC program (entry point) .

do two things: 
  1. Open a local web page on this computer (http://127.0.0.1:8766) ,is the operation screen;
  2. Leave the operations on the screen to Application: it connects to RPi on plasmic_tcpd (On the plasma side,TCP/IP) 
     and the pump control board plugged into this computer (Arduino Mega,USB) .

This file only starts and ends: read the command line, first occupy the port of the web page (the same computer can only open one copy), create Application, 
When the black window is closed, first stop the pump and high pressure. The rest are in application.py (General Manager) and httpd.py (web server).

“allows opening of real ports. Only this file will do it, and only when it is not in practice mode (--demo) when.
Installation program and testing session import This file:import is set.
"""
from __future__ import annotations

import os
import sys

# The bundled (embeddable) Python does not put the script's folder on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True  # never a __pycache__ in the program folder, however this file is started

import argparse  # noqa: E402
import getpass  # noqa: E402
import json  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import webbrowser  # noqa: E402
from datetime import datetime  # noqa: E402,F401
from pathlib import Path  # noqa: E402

import httpd  # noqa: E402
import netcheck  # noqa: E402,F401
import pinlock  # noqa: E402
import plasmic_client  # noqa: E402,F401
import pumpboard  # noqa: E402
import schedule  # noqa: E402,F401
from application import Application  # noqa: E402
from httpd import ExclusiveHTTPServer, make_handler  # noqa: E402,F401
from pinlock import PinLock  # noqa: E402
from store import (DEFAULT_CONFIG, RUN_ID, Store, now_iso, site_config, validate_config,  # noqa: E402,F401
                   write_json)

VERSION = '2.0.0'
ROOT = Path(__file__).resolve().parent
# Windows tells a console program that its window is being closed, the user logs off, the computer shuts
# down, or Ctrl+Break was pressed. (Ctrl+C stays Python's: KeyboardInterrupt, then the normal way out.)
CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT = 1, 2, 5, 6
CLOSING_EVENTS = (CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT)
T_CLOSING = 'Stopping pump and high pressure...'
# A second start (AMENDMENTS M17). The port is what tells two copies apart, so the second one asks the holder.
PORT_WAIT_S = 10.0       # a copy whose window was closed: Windows gives it five seconds, then it is gone
PORT_GRACE_S = 2.0       # the port is held by something that does not answer as this program: it may just be gone
PORT_PROBE_S = 1.5       # how long the holder has to answer
PORT_POLL_S = 0.25
MODE_NAME = {True: 'practice mode', False: 'official mode'}
T_PORT_TAKEN = 'port {port} is already used by another program (maybe a Plasmic electronic control). Please close it first.'
T_PORT_WAIT = 'Previous Plasmic The electronic control is ending (stop the pump first and turn off the high voltage). Wait for it a few seconds...'
T_PORT_STARTING = 'another Plasmic The electronic control is starting: the operation screen will open automatically after a while. This window can be closed.'
T_PORT_OPEN = 'Plasmic The electronic control is on ({mode}) ,did not open another one. Operation screen:{url}'
T_PORT_OPEN_HINT = 'This window can be closed; the original black window is the program itself. If the error occurs after'
T_PORT_OTHER = '{mode}is still on, it uses the same port {port}.Please close its black window and open it again.'
_console_handler = None  # kept for the life of the program: Windows calls it from a thread of its own


def port_holder(port):
    """What holds the port, as far as it tells: ('running', its demo flag) · ('starting', None) · ('closing', None) ·
    ('other', None) for anything that does not answer as this program does."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never through a proxy: it is this PC
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/bootstrap', timeout=PORT_PROBE_S) as reply:
            data = json.load(reply)
    except urllib.error.HTTPError as error:
        try:
            data = json.load(error)
        except Exception:
            return 'other', None
        code = data.get('code') if isinstance(data, dict) else None
        return (code, None) if code in ('starting', 'closing') else ('other', None)
    except Exception:
        return 'other', None
    if isinstance(data, dict) and isinstance(data.get('demo'), bool) and data.get('version'):
        return 'running', data['demo']
    return 'other', None


def claim_port(port, demo, show):
    """The listening socket of this copy — or the end of this copy (SystemExit) when another one has the port.

    A copy whose window was just closed still holds the port while it stops the pumps and the high voltage: this
    one waits for it, and touches nothing before it has the port. A copy that runs is not doubled: its screen is
    opened instead (`show`), or — when it is the other mode — the person is told which one to close."""
    began, until, told = None, None, False
    while True:
        try:
            return ExclusiveHTTPServer(('127.0.0.1', port), httpd.NotReady)
        except OSError:
            pass
        if began is None:
            began = time.monotonic()
            until = began + PORT_GRACE_S
        kind, other = port_holder(port)
        if kind == 'running':
            if other != demo:
                raise SystemExit(T_PORT_OTHER.format(mode=MODE_NAME[other], port=port))
            url = f'http://127.0.0.1:{port}/'
            print(T_PORT_OPEN.format(mode=MODE_NAME[demo], url=url), flush=True)
            print(T_PORT_OPEN_HINT, flush=True)
            if show:
                webbrowser.open(url)
            raise SystemExit(0)
        if kind == 'starting':
            print(T_PORT_STARTING, flush=True)
            raise SystemExit(0)
        if kind == 'closing' and not told:
            told = True
            until = time.monotonic() + PORT_WAIT_S
            print(T_PORT_WAIT, flush=True)
        if time.monotonic() >= until:
            raise SystemExit(T_PORT_TAKEN.format(port=port))
        time.sleep(PORT_POLL_S)


def console_event(app, closing=None):
    """What happens when the black window is closed (SPEC B.11): everything off within the few seconds
    Windows allows. Returns the function Windows is given; False = "then end the program as usual".
    `closing()` is called first: from then on the web server says that the program is ending (M17)."""
    def on_event(kind):
        if kind not in CLOSING_EVENTS:
            return False
        try:
            if closing is not None:
                closing()
        except Exception:
            pass
        try:
            print(T_CLOSING, flush=True)
        except Exception:
            pass
        try:
            app.close(fast=True)
        except Exception:
            pass
        return False
    return on_event


def install_console_handler(app, closing=None):
    global _console_handler
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes
    _console_handler = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(console_event(app, closing))
    return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True))


def main():
    parser = argparse.ArgumentParser(description='Plasmic electronic control (computer)')
    parser.add_argument('--demo', action='store_true', help='Simulation mode: Start the simulator on this machine without connecting to any device')
    parser.add_argument('--port', type=int, default=8766, help='The port of the local web page')
    parser.add_argument('--data', help='folder (default data; simulation mode is data-demo) ')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--set-pin', action='store_true', help='Setup maintenance PIN (6–8 digits)')
    parser.add_argument('--sim-port', type=int, default=8767, help='is only used in simulation mode:RPi emulator port')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:  # messages are Chinese; never depend on the console's code page
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    data_dir = Path(args.data) if args.data else ROOT / ('data-demo' if args.demo else 'data')
    if args.set_pin:
        pin = getpass.getpass('New Repair PIN (6–8 digits, not displayed when input):')
        if pin != getpass.getpass('Enter again: '):
            parser.error('Two inputs are different')
        try:
            PinLock(data_dir / 'pin.json').set(pin)
        except ValueError as exc:
            parser.error(str(exc))
        print(pinlock.SAVED_TEXT)
        return
    # The port first: it is what tells a second copy of the program apart. A second copy must stop here,
    # before it has marked the first one's schedule as interrupted or touched the equipment. (It waits here for
    # a copy that is just ending, and opens the screen of one that runs: claim_port.)
    server = claim_port(args.port, args.demo, not args.no_browser)
    if not args.demo:
        pumpboard.allow_real_ports()
    try:
        app = Application(data_dir, args.demo, pump_mode='sim' if args.demo else 'real', sim_port=args.sim_port,
                          keep_awake=True)
    except BaseException:
        server.server_close()
        raise
    server.RequestHandlerClass = make_handler(app, secrets.token_urlsafe(32))
    install_console_handler(app, lambda: setattr(server, 'RequestHandlerClass', httpd.Closing))
    url = f'http://127.0.0.1:{args.port}/'
    print(f'Plasmic Electronic control {VERSION}: {url}' + ('   (simulation mode)' if args.demo else ''), flush=True)
    print('Closing this window will end the program.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()


if __name__ == '__main__':
    main()
