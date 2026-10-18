#!/usr/bin/env python3
"""Plasmic electronic control — and RPi ((plasma side) TCP/IP connection.

was originally app.py inside Link,Move the entire paragraph and change the name RpiLink (Link or its alias): connection, disconnection and reconnection, automatic search,
asks the status once every second, just turned on Nano Automatically synchronize parameters, stop the discharge of ”someone is watching” when the screen disappears, stop the command and go to another
New connection.TCP The agreement has not changed (plasmic_client.py,protocol 2) .

2.0.0 added (SPEC B.2) : 
  command(..., claim=True)   This job is saved by the caller; the connected thread will not be saved again.
  rpi_ready(), wait_job()    The text of the two sections in the original schedule (whether it can be discharged and waiting for the end of a measurement) has not been changed.
  fetch_result()             Read back the measurement results, failure interval 2 Read again every second; never re-discharge.
  hv_view()                  Is the high voltage ”OK” now? (SPEC B.4 table). It depends on whether the pump can move or not.
  flow_view(), params_view() RPi Which measurement process, parameters and Nano The one above is different: You don’t have to compare the pictures by yourself.
  rewrite_fix()              RPi ’s correction suggestions are replaced by those on the current screen.
  When the set voltage on the high-voltage power supply panel is different from the parameters (the power is turned off), please ask here RPi use RS485 Write it as the voltage of the parameter (M22) .
  RPi The device status I read is too old (more than 8 seconds), it will be asked to read it again, so there is no need for a ”read status” button on the screen.

This file does not send any command that will output high voltage;plasma_run Only when someone else calls command() will be sent out. When
"""
from __future__ import annotations

import re
import socket
import threading
import time

import netcheck
import plasmic_client
from plasmic_client import Client, PlasmicError

POLL_SECONDS = 1.0
UNEXPECTED_WAIT_SECONDS = 2.0   # the link thread starts over this long after an error nobody foresaw
# While the RPi cannot be reached: how often to ask the local network where it is.
SEARCH_SECONDS = 20.0
# During a discharge started from this screen: if the screen stops asking for updates for this
# long (browser closed or frozen), nobody can press STOP any more, so the program stops the job.
UI_WATCHDOG_SECONDS = 10.0
# The RPi reads the equipment by itself every poll_s, which its settings allow up to 60 s. When its
# reading is older than FRESH_AGE_SECONDS the next status asks for a new one, at most every
# FRESH_EVERY_SECONDS.
FRESH_AGE_SECONDS = 8.0
FRESH_EVERY_SECONDS = 5.0
# hv_view(): how old the newest status may be (received here / read on the RPi) to count as known.
HV_RECEIVED_SECONDS = 5.0
HV_SNAPSHOT_SECONDS = 10.0
# wait_job(): after STOP the RPi's tool switches everything off by itself; it gets this long for it.
STOP_WAIT_SECONDS = 60.0
# fetch_result(): the pause before the one second try.
RESULT_RETRY_SECONDS = 2.0
CANCEL_REASONS = {'stop': 'Stop pressed', 'watchdog_timeout': 'The control connection timed out and the server automatically stopped.',
                  'owner_disconnected': 'The control connection is interrupted and the server automatically stops', 'shutdown': 'RPi Server is down'}
# [parameter.json key, key in status.snapshot.parameter, key in the Nano's active_parameters, Nano value ÷]
PARAMETERS = (('Ontime', 'on_time_ms', 'on_time_us', 1000), ('Offtime', 'off_time_ms', 'off_time_us', 1000),
              ('Cycle', 'cycles', 'cycles', 1), ('Loopnum', 'loops', 'loop_count', 1),
              ('Integration_Time', 'integration_ms', 'integration_time_us', 1000),
              ('Spectra_Interval', 'spectra_interval_ms', 'spectra_interval_us', 1000),
              ('Voltage', 'voltage_v', 'voltage_display_v', 1))
# hello.flow -> (background frames, words for the screen)
FLOWS = {'unified': (1, 'Turn on the high voltage first and shoot the background 1 open'), 'verified': (3, 'Old Process: Background 3 open')}
FAULT_NAMES = {'HEARTBEAT_LOST': 'Heartbeat interruption', 'TRIGGER_TIMEOUT': 'trigger timeout', 'JOB_TIMEOUT': 'work timeout'}
# The RPi's "fix" texts name buttons of the 1.3.0 screen. What to say instead; applied longest first.
FIXES = tuple(sorted((
    ('Wait for the voltage to drop to 0 and then click ”Read Status”', 'Wait until the voltage drops to 0,The program will re-read itself'),
    ('Click ”Read Status”; if it still fails, please check RS485 USB and RPi', 'Wait a few seconds, the program will re-read itself; if it still fails, please check RS485 USB and RPi'),
    ('Click Clear Nano fault”, then click ”Synchronize Nano parameter”', 'Ask maintenance personnel to go to ”Repair › Plasma Parameters” click ”Clear” Nano fault”, then click ”Sync to Nano”'),
    ('Press ”HSPY OFF＋Nano STOP”', 'Press ”Stop” at the bottom STOP”'),
    ('in ”Plasma Parameters”', 'Ask maintenance personnel to go to ”Repair › Plasma Parameters”'),
    ('re-save it in ”Plasma Parameters” (will write HSPY set value) ',
     'Wait a few seconds and the program will change it by itself. There is no change: Click ”Measurement Content” in ”Scheduled Measurement” and click ”Save and Sync”'),
    ('examine HSPY Is the power on?RS485 Wiring and Addressing 1',
     'Turn on the high voltage power supply (HSPY) ’s power switch, and then click ”Recheck”.'
     'is on but still no response: look behind it RS485 of A, B Are the two lines loose or connected reversely? Is the communication address correct? 1, rate 9600'),
), key=lambda pair: -len(pair[0])))
# A reason of the RPi that needs other words on this screen (AMENDMENTS M17): (how it starts, the sentence shown).
# The supply that does not answer is switched off at its mains switch — the usual state between measurements.
REASONS = (
    ('HSPY Power readback is invalid', 'high voltage power supply (HSPY) No response: The power is not on, or its RS485 line is not connected properly'),
)
# The supply's panel says another set voltage than the parameters (AMENDMENTS M22). The RPi of today does not call
# that a reason any more (the measurement writes it); an older one, or the fall-back flow, still does.
VOLTS_DIFFER = re.compile(r'parameter\.json Voltage (\S+) V and HSPY set value (\S+) V inconsistent')
T_VOLTS_DIFFER = 'high-voltage power supply panel is {found} V,program is {wanted} V'
# Events of the automatic write (RpiLink._sync_voltage).
E_VOLTS_WRITTEN = 'high-voltage power supply panel was originally {found} V,Used RS485 changed to {wanted} V (The power is off and there is no output)'
E_VOLTS_FAILED = 'high-voltage power supply panel is {found} V,is not automatically changed to {wanted} V ({why}) .The program will be written again before discharge; if it cannot be written in, the high voltage will not be turned on.'
E_VOLTS_OLD = 'RPi will not change the setting voltage of the high-voltage power supply by itself: please double-click first. The program on Install.cmd renew RPi'
# Alarms only this connection can see in time (SPEC B.9): (key, priority, area, kind, text).
A_LINK_LOST = ('rpi_link_lost', 3, 'RPi', 'condition', 'RPi The connection was interrupted and the program is reconnecting.')
A_OLD = ('rpi_old', 3, 'RPi', 'condition', 'RPi is still an old version: please double-click first Install.cmd renew RPi')
A_FALLBACK = ('rpi_flow_fallback', 3, 'RPi', 'condition', 'RPi Set to the old measurement process: Background 3 sheets, click to make approx. Stop automatically after 11 Discharge in seconds')
A_NANO = ('nano_fault', 2, 'high pressure', 'condition', 'Nano Fault: {name}.Ask maintenance personnel to go to ”Repair › Plasma Parameters” cleared.')


def rewrite_reason(text):
    """A reason of the RPi's `ready.problems` in plain words, where this program has some (AMENDMENTS M17)."""
    if not isinstance(text, str):
        return text
    for start, plain in REASONS:
        if text.startswith(start):
            return plain
    match = VOLTS_DIFFER.match(text)
    if match:
        return T_VOLTS_DIFFER.format(found=_short(match.group(2)), wanted=_short(match.group(1)))
    return text


def _short(number):
    """'1000.0' -> '1000': a number as people write it; anything else as it is."""
    try:
        return '%g' % float(number)
    except (TypeError, ValueError):
        return str(number)


# ── “Recheck” (AMENDMENTS M18): what a reading taken now says, for someone who stands at the rig ──
CHECK_POWER, CHECK_NANO = 'high voltage power supply (HSPY) ', 'Nano controller'
C_SILENT = 'No response'
C_POWER = 'has response: output Is {state},{volts} V (settings {set_volts} V) '
C_GARBLED = 'Something came back, but I can’t read it ({error}) '
C_NANO = 'responded:{state}'
C_NANO_FAULT = 'responded: fault ({fault}) '
C_BUSY = 'RPi Measurement is being performed, and the device cannot be read at this time. Wait for it to finish and press again.'
C_BOTH = 'high voltage power supply and Nano all responded.'
C_BOTH_SILENT = 'high voltage power supply and Nano No response: See first RS485 adapter USB plugged in? RPi , look at those two lines again.'
C_POWER_SILENT = ('Nano has a response, so RPi, RS485 The adapter and the line are connected; the problem is in the high-voltage power supply section. Please see:'
                  '① Is the panel of the high-voltage power supply illuminated?② Behind it RS485 of A, B Check whether the two wires are loose or connected reversely (then debug if there is still no connection);'
                  '③ Is the communication address on the panel 1, rate 9600.')
C_POWER_GARBLED = 'There is something in the high-voltage power supply, but the content is wrong: most likely A, B Line contact is poor or the speed is not 9600.'
C_NANO_SILENT = 'High voltage power supply responds,Nano None: See Nano power supply,and its RS485 module line.'
C_NOT_READY = 'cannot be discharged yet:{reasons}'
NANO_WORDS = {'IDLE': 'standby', 'ARMED': 'Ready', 'WAIT_TRIGGER': 'Waiting for trigger', 'RUNNING': 'Outputting', 'DONE': 'Complete', 'FAULT': 'fault'}


def check_view(status):
    """What a status of the RPi says about the supply and the Nano, in plain words.

    -> {'ok', 'busy', 'rows': [{'name', 'ok', 'text'} for the supply and the Nano], 'advice'}. `ok`: both answer."""
    status = status if isinstance(status, dict) else {}
    if status.get('busy'):
        return {'ok': False, 'busy': True, 'rows': [], 'advice': C_BUSY}
    snapshot = status.get('snapshot') if isinstance(status.get('snapshot'), dict) else {}
    power = snapshot.get('power') if isinstance(snapshot.get('power'), dict) else {}
    nano = snapshot.get('nano') if isinstance(snapshot.get('nano'), dict) else {}

    def number(value):
        return '%g' % value if _number(value) else '—'

    def silent(row):                             # nothing came back, as opposed to something unreadable
        error = str(row.get('error') or '')
        return not error or 'Timed out' in error or 'timeout' in error.lower()
    power_ok, nano_ok = power.get('valid') is True, nano.get('valid') is True
    if power_ok:
        power_text = C_POWER.format(state='OFF' if power.get('run_stop_setting') == 0 else 'ON',
                                    volts=number(power.get('voltage_v')), set_volts=number(power.get('set_voltage_v')))
    else:
        power_text = C_SILENT if silent(power) else C_GARBLED.format(error=str(power.get('error'))[:80])
    if nano_ok:
        faulty = nano.get('state') == 'FAULT' or nano.get('fault') not in (None, 'NONE')
        nano_text = (C_NANO_FAULT.format(fault=nano.get('fault')) if faulty
                     else C_NANO.format(state=NANO_WORDS.get(nano.get('state'), str(nano.get('state')))))
    else:
        nano_text = C_SILENT if silent(nano) else C_GARBLED.format(error=str(nano.get('error'))[:80])
    if power_ok and nano_ok:
        ready = status.get('ready') if isinstance(status.get('ready'), dict) else {}
        problems = [rewrite_reason(str(row.get('reason', ''))) for row in ready.get('problems') or [] if isinstance(row, dict)]
        advice = C_BOTH if ready.get('plasma') or not problems else C_BOTH + C_NOT_READY.format(reasons='; '.join(problems))
    elif nano_ok:
        advice = C_POWER_SILENT if silent(power) else C_POWER_GARBLED
    elif power_ok:
        advice = C_NANO_SILENT
    else:
        advice = C_BOTH_SILENT
    return {'ok': power_ok and nano_ok, 'busy': False, 'advice': advice,
            'rows': [{'name': CHECK_POWER, 'ok': power_ok, 'text': power_text},
                     {'name': CHECK_NANO, 'ok': nano_ok, 'text': nano_text}]}


def rewrite_fix(text):
    """A "fix" text of the RPi with the names of today's buttons (SPEC B.2)."""
    if not isinstance(text, str):
        return text
    for old, new in FIXES:
        text = text.replace(old, new)
    return text


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def hv_state(connected, status, received_s):
    """SPEC B.4 "High-voltage view": {'known', 'idle', 'reasons'}; the first row that matches decides.

    ``status`` is the RPi's status reply, ``received_s`` how long ago this computer received it.
    Pumps may only move while this says known and idle.
    """
    def answer(known, idle, reason=None):
        return {'known': known, 'idle': idle, 'reasons': [reason] if reason else []}
    if not connected:
        return answer(False, False, 'RPi Not connected')
    if not isinstance(status, dict) or not _number(received_s) or received_s > HV_RECEIVED_SECONDS:
        return answer(False, False, 'RPi status exceeds 5 Not received in seconds No update for')
    job = status.get('job') or {}
    if job.get('state') == 'running' and job.get('high_voltage'):
        return answer(True, False, 'is discharging')
    if not _number(status.get('age_s')) or status['age_s'] > HV_SNAPSHOT_SECONDS:
        return answer(False, False, 'RPi The device status read exceeds 10 seconds')
    snapshot = status.get('snapshot') or {}
    power, nano = (snapshot.get(key) or {} for key in ('power', 'nano'))
    # A device that answers and reads ON decides, whatever the other one does: "not known" is what an
    # operator may overrule with a tick (the supply is switched off at its mains switch, say), and a
    # tick must never overrule a supply that reads ON or a Nano that is armed or pulsing.
    if power.get('valid') is True:
        if power.get('run_stop_setting') != 0:
            return answer(True, False, 'high voltage power supply is ON')
        if power.get('voltage_v') != 0 or power.get('current_a') != 0:
            return answer(True, False, 'High voltage output has not returned to zero yet')
    if nano.get('valid') is True and (nano.get('state') not in ('IDLE', 'DONE', 'FAULT')
                                      or nano.get('output_command_high') is not False
                                      or nano.get('output_pin_sample') is not False):
        return answer(True, False, 'Nano is still outputting or is ready')
    if power.get('valid') is not True or nano.get('valid') is not True:
        return answer(False, False, 'high voltage power supply/Nano is invalid Press the password of')
    return answer(True, True)


def flow_state(connected, hello, status):
    """How the RPi measures: {'name', 'ok', 'background_frames', 'text'}. Not ok: no discharge may be sent."""
    if not connected or not isinstance(hello, dict):
        return {'name': None, 'ok': False, 'background_frames': None, 'text': 'RPi Not connected'}
    name = hello.get('flow')
    if not isinstance(name, str) or not name:  # the program of 1.3.0: it would measure in an order nobody chose
        return {'name': None, 'ok': False, 'background_frames': None, 'text': 'RPi is still an old version'}
    frames, text = FLOWS.get(name, (None, None))
    if hello.get('flow_error'):
        return {'name': name, 'ok': False, 'background_frames': frames, 'text': str(hello['flow_error'])}
    if text is None:
        return {'name': name, 'ok': False, 'background_frames': None, 'text': f'This program does not recognize RPi “ A hash of{name}”'}
    start = (status or {}).get('start_s')
    if _number(start):
        text += f',Click to approx. {start:g} seconds discharge'
    return {'name': name, 'ok': True, 'background_frames': frames, 'text': text}


def params_state(status):
    """{'values', 'nano_synced'}: the seven values of the RPi's parameter.json, and whether the Nano holds the same."""
    snapshot = (status or {}).get('snapshot') or {}
    parameter, nano = (snapshot.get(key) or {} for key in ('parameter', 'nano'))
    if parameter.get('valid') is not True or not all(_number(parameter.get(key)) for _name, key, _nano, _by in PARAMETERS):
        return {'values': None, 'nano_synced': None}
    values = {name: parameter[key] for name, key, _nano, _by in PARAMETERS}
    if nano.get('valid') is not True:
        return {'values': values, 'nano_synced': None}
    active = nano.get('active_parameters')
    if not isinstance(active, dict) or not active:
        return {'values': values, 'nano_synced': False}  # a Nano that was just switched on holds none
    same = all(_number(active.get(key)) and abs(parameter[source] - active[key] / by) <= 1e-9
               for _name, source, key, by in PARAMETERS)
    return {'values': values, 'nano_synced': same}


class RpiLink:
    """The TCP/IP connection to the RPi: connects, reconnects, polls status once a second."""

    def __init__(self, store, search=True, auto_sync=True):
        self.store = store
        self.request_lock = threading.Lock()  # one question at a time on the connection
        self.lock = threading.Lock()
        self.client = None
        self.error = 'Connecting...'
        self.hello = None
        self.status = None
        self.status_mono = None
        self.pending = ''
        self.own_job = None
        self.search = search        # look for the RPi on the network while it cannot be reached
        self.found = []
        self.searched = float('-inf')
        self.addresses = []
        self.addresses_mono = float('-inf')
        self.auto_sync = auto_sync  # load the parameters into a Nano that has none; write the voltage into the supply
        self.sync_tried = False
        self.voltage_tried = None   # (set voltage found on the supply, voltage wanted) of the last automatic write
        self.ui_seen = time.monotonic()
        self.ui_stop_sent = None
        self.handled = None  # job ids already saved or reported; None until the first status arrives
        self.claimed = set()  # jobs somebody took with command(claim=True) before that first status
        self.was_connected = False  # connected at least once since the program started
        self.fresh_asked = float('-inf')
        # Optional listener for the alarms of this connection, set by whoever owns the alarm list:
        # on_alarm('raise', key, priority, area, text, kind) / on_alarm('clear', key).
        self.on_alarm = None
        self.told = {}  # alarm key -> the text that was last passed on
        self.retry = 2.0
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._loop, name='rpi-link', daemon=True)
        self.thread.start()

    # ---- connection
    def _connect(self):
        config = self.store.config
        client = Client(config['rpi_host'], config['rpi_port'], config['token'], timeout=3)
        where = f'{config["rpi_host"]}:{config["rpi_port"]}'
        try:
            hello = client.connect()
        except PlasmicError as exc:
            return self._failed(f'RPi Deny connection: {exc}')
        except socket.timeout:
            return self._failed(f'Unable to connect {where} (timeout): Check the network cable and computer network card IP,as well as RPi Is it powered on?')
        except ConnectionRefusedError:
            return self._failed(f'{where} Deny connection: RPi has a response, but TCP The server is not running ( Allowlist forsudo systemctl status plasmic-tcpd) ')
        except (OSError, ValueError) as exc:
            return self._failed(f'Unable to connect {where}: {exc}')
        with self.lock:
            self.client, self.hello, self.error, self.retry = client, hello, '', 2.0
            self.found, self.sync_tried, self.voltage_tried = [], False, None
            self.was_connected = True
        self.store.event(f'is connected to RPi {where} (server {hello.get("version")}{",simulation mode" if hello.get("sim") else ""}) ')

    def _failed(self, message):
        with self.lock:
            changed = message != self.error
            self.error = message
        if changed:
            self.store.event(message, 'warn')

    def _look_around(self):
        """Not connected: ask the network where the RPi is (one small broadcast, see netcheck)."""
        config = self.store.config
        local = config['rpi_host'].startswith('127.') or config['rpi_host'] == 'localhost'
        if not self.search or local or time.monotonic() - self.searched < SEARCH_SECONDS:
            return
        self.searched = time.monotonic()
        direct = [config['rpi_host']] if netcheck.IPV4.fullmatch(config['rpi_host']) else []
        try:
            found = netcheck.discover(config['rpi_port'], hosts=direct)
        except (OSError, ValueError):
            found = []
        with self.lock:
            changed = [row['host'] for row in found] != [row['host'] for row in self.found]
            self.found = found
        elsewhere = [row['host'] for row in found if row['host'] != config['rpi_host']]
        if changed and elsewhere:
            self.store.event(f'Found on the Internet RPi: {", ".join(elsewhere)} (is {config["rpi_host"]}) ', 'warn')

    def local_addresses(self):
        """This computer's IPv4 addresses, looked up at most every 10 s."""
        if time.monotonic() - self.addresses_mono > 10:
            self.addresses, self.addresses_mono = netcheck.local_ipv4(), time.monotonic()
        return self.addresses

    def _drop(self, reason):
        with self.lock:
            client, self.client = self.client, None
            self.status = self.status_mono = None
            self.error = 'and RPi ’s connection is interrupted: Measurement process of' + reason
        if client is not None:
            client.close()
            self.store.event('and RPi ’s connection is interrupted: Measurement process of' + reason, 'warn')

    def reconnect(self):
        with self.request_lock:
            self._drop('Connection settings have been changed')
        with self.lock:
            self.error, self.retry = 'Connecting...', 2.0
        self.wake.set()

    def _loop(self):
        while not self.closed.is_set():
            if self.client is None:
                self._connect()
                if self.client is None:
                    self._tell(A_LINK_LOST, self.was_connected)
                    self._look_around()
                    self.wake.wait(self.retry)
                    self.wake.clear()
                    self.retry = min(10.0, self.retry * 1.5)
                    continue
                self._tell(A_LINK_LOST, False)
            try:
                fields = self._status_fields()
                with self.request_lock:
                    client = self.client
                    status = client.request('status', **fields) if client else None
                if status is not None:
                    self._on_status(status)
            except PlasmicError as exc:
                self._failed('RPi Return:' + str(exc))
            except (ConnectionError, OSError, ValueError) as exc:
                self._drop(str(exc))
            except Exception as exc:
                # Anything else (a status of a shape this program does not expect, say) used to end this
                # thread without a word: the page went on showing "connected" with a status that never
                # changed. Drop the connection instead and start over; _drop() writes the event only
                # when a connection was open, so a fault that stays does not fill the log.
                self._drop('read RPi state (%s: %s) ' % (type(exc).__name__, exc))
                self.wake.wait(UNEXPECTED_WAIT_SECONDS)
                self.wake.clear()
                continue
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def _status_fields(self):
        """{'fresh': True} when the RPi's own reading of the equipment has grown old, else {}.

        Not while a job runs (the RPi cannot read then) nor while a command waits for the connection,
        and not more often than every FRESH_EVERY_SECONDS.
        """
        with self.lock:
            status, pending = self.status, self.pending
        if (status is None or pending or status.get('busy')
                or time.monotonic() - self.fresh_asked < FRESH_EVERY_SECONDS):
            return {}
        if _number(status.get('age_s')) and status['age_s'] <= FRESH_AGE_SECONDS:
            return {}
        self.fresh_asked = time.monotonic()
        return {'fresh': True}

    # ---- state
    def _on_status(self, status):
        status.pop('ok', None)
        with self.lock:
            self.status, self.status_mono, self.error = status, time.monotonic(), ''
        self._watch(status)
        job = status.get('job')
        if self.handled is None:
            # First status after start: an old failure is not news, an unsaved result still is.
            known = self.store.run_ids()
            with self.lock:
                self.handled = known | self.claimed
            if job and job['state'] != 'done':
                self.handled.add(job['id'])
        if not job or job['state'] != 'running':
            self._sync_new_nano(status)
            self._sync_voltage(status)
        if not job:
            return
        if job['state'] == 'running':
            quiet = time.monotonic() - self.ui_seen
            if (job['high_voltage'] and job['id'] == self.own_job and self.ui_stop_sent != job['id']
                    and quiet > UI_WATCHDOG_SECONDS):
                self.ui_stop_sent = job['id']
                self.store.event(f'The operation screen exceeds {UI_WATCHDOG_SECONDS:.0f} has not responded for seconds and has automatically stopped discharging.', 'error')
                try:
                    self.stop()
                except RuntimeError as exc:
                    self.store.event(str(exc), 'error')
        elif job['id'] not in self.handled:
            self.handled.add(job['id'])
            self._finished(job)

    def _sync_new_nano(self, status):
        """A Nano that was just switched on holds no parameters: its LCD shows "--" and "POWER ?".

        Load them once, without anybody having to press a button. This is the same step as
        “synchronized to Nano”: the RPi tool refuses unless the supply is OFF at 0 V and the Nano is
        idle, and it never arms or starts an output. Tried once per Nano power-up; a failure is
        only noted and the button stays available.
        """
        snapshot = status.get('snapshot') or {}
        nano, power, parameter = (snapshot.get(key) or {} for key in ('nano', 'power', 'parameter'))
        if nano.get('valid') is not True:
            return
        if nano.get('active_parameters'):
            self.sync_tried = False
            return
        if (not self.auto_sync or self.sync_tried or status.get('busy') or self.pending
                or nano.get('firmware') not in ('1.2.0', '1.2.1') or nano.get('state') != 'IDLE'
                or nano.get('fault') != 'NONE' or power.get('valid') is not True
                or power.get('run_stop_setting') != 0 or power.get('voltage_v') != 0
                or parameter.get('valid') is not True):
            return
        self.sync_tried = True
        try:
            self.command('prepare')
            self.store.event('Nano Just turned on, no parameters yet: The parameters of the configuration file have been automatically synchronized (no discharge).LCD will display the parameters and POWER OFF')
        except (RuntimeError, PlasmicError) as exc:
            self.store.event(f'Nano There are no parameters yet, and the automatic synchronization was not successful ({exc}) ; Ask maintenance personnel to go to ”Repair › Plasma Parameters” press ”Synchronize to Nano”', 'warn')

    def _sync_voltage(self, status):
        """The supply's panel holds another set voltage than the parameters (somebody turned its knob, or it came
        back from its mains switch with an old number): write ours into it, without anybody pressing a button.

        The person, 2026-10-07, with 1000 V on the panel and 30 V here: “There should be no error message, just useRS485Change it and enter it
        voltage”. The RPi writes only while the supply is OFF with nothing at its output, reads the number back and
        switches nothing on (plasma_measure.py --set-voltage). The measurement does the same by itself before it
        switches on, so a write that fails here only leaves the panel as it was. Tried once for each pair of numbers.
        """
        snapshot = status.get('snapshot') or {}
        power, parameter = (snapshot.get(key) or {} for key in ('power', 'parameter'))
        found, wanted = power.get('set_voltage_v'), parameter.get('voltage_v')
        if (power.get('valid') is not True or parameter.get('valid') is not True or not _number(found)
                or type(wanted) is not int or found == wanted):
            self.voltage_tried = None
            return
        if (not self.auto_sync or self.voltage_tried == (found, wanted) or status.get('busy') or self.pending
                or not 1 <= wanted <= 700 or power.get('run_stop_setting') != 0 or power.get('voltage_v') != 0
                or power.get('current_a') != 0):
            return
        self.voltage_tried = (found, wanted)
        words = {'found': _short(found), 'wanted': wanted}
        try:
            reply = self.command('sync_voltage')
        except PlasmicError as exc:
            if exc.code == 'busy':           # the bus was taken that moment: the next status tries again
                self.voltage_tried = None
            elif exc.code == 'unknown_command':
                self.store.event(E_VOLTS_OLD, 'warn')
            else:
                self.store.event(E_VOLTS_FAILED.format(why=exc, **words), 'warn')
            return
        except RuntimeError:                 # the connection went away: the next one starts anew
            return
        if reply.get('changed'):
            self.store.event(E_VOLTS_WRITTEN.format(**words))

    def _finished(self, job):
        """A job nobody claimed has ended (a capture from Maintenance › spectrum, or a job another program started)."""
        name = 'discharge' if job['kind'] == 'plasma' else 'Spectrum acquisition'
        if job['state'] == 'done':
            try:
                with self.request_lock:
                    client = self.client
                    if client is None:  # dropped by another thread a moment ago
                        raise ConnectionError('Connection interrupted')
                    result = client.request('result', job=job['id'], frames=True)
                meta = self.store.save_run(result, source='manual' if job['kind'] == 'plasma' else 'check')
                wait = meta.get('wait_s')
                self.store.event(f'{name}Completed, the spectrum has been archived ({job["id"][:12]}…,highest peak {meta["peak_nm"]:.1f} nm) '
                                 + (f'; After pressing {wait:.1f} starts discharging in seconds' if isinstance(wait, (int, float)) else ''))
            except (PlasmicError, ConnectionError, OSError, ValueError, KeyError) as exc:
                self.store.event(f'{name}Completed, but failed to read the spectrum:{exc} (data is still available RPi,Can be reread later) ', 'error')
            return
        text = f'{name}Not completed:{CANCEL_REASONS.get(job["cancel_reason"], "")} {job["error"]}'.strip()
        post = job.get('post_stop')
        if post and not post.get('ok'):
            text += f'; after stopping OFF readback unconfirmed ({post.get("error")}) — Please confirm physically that the high voltage has been turned off'
        self.store.event(text, 'error')

    def _watch(self, status):
        """Pass on the alarms that follow from what the RPi says about itself (only when somebody listens)."""
        if self.on_alarm is None:
            return
        flow = (self.hello or {}).get('flow')
        self._tell(A_OLD, not flow)
        self._tell(A_FALLBACK, flow == 'verified')
        nano = (status.get('snapshot') or {}).get('nano') or {}
        fault = nano.get('fault')
        if nano.get('valid') is True and isinstance(fault, str):  # a Nano that cannot be read says nothing either way
            self._tell(A_NANO, fault != 'NONE', A_NANO[4].format(name=FAULT_NAMES.get(fault, fault)))

    def _tell(self, alarm, holds, text=None):
        """Raise or clear one alarm through on_alarm. Only a change is told, and it counts once it was heard."""
        listener = self.on_alarm
        if listener is None:
            return
        key, priority, area, kind, usual = alarm
        wanted = (text or usual) if holds else None
        if self.told.get(key) == wanted:
            return
        try:
            if wanted is None:
                listener('clear', key)
            else:
                listener('raise', key, priority, area, wanted, kind)
            self.told[key] = wanted
        except Exception:
            pass  # whatever the listener does, the connection goes on

    def ui_alive(self):
        self.ui_seen = time.monotonic()

    def latest(self, max_age=10.0):
        """The newest status from the RPi; None without a connection or when it has gone stale."""
        with self.lock:
            if self.client is None or self.status is None or time.monotonic() - self.status_mono > max_age:
                return None
            return self.status

    def _now(self):
        """(connected, hello, status, seconds since that status was received) at one moment."""
        with self.lock:
            age = None if self.status_mono is None else time.monotonic() - self.status_mono
            return self.client is not None, self.hello, self.status, age

    def hv_view(self):
        """Is the high voltage known to be off right now? {'known', 'idle', 'reasons'} (SPEC B.4)."""
        connected, _hello, status, age = self._now()
        return hv_state(connected, status, age)

    def flow_view(self):
        """How the RPi measures: {'name', 'ok', 'background_frames', 'text'} (SPEC B.2)."""
        connected, hello, status, _age = self._now()
        return flow_state(connected, hello, status)

    def params_view(self):
        """{'values', 'nano_synced'}: the plasma parameters on the RPi and whether the Nano holds the same."""
        return params_state(self.latest())

    @property
    def connected(self):
        """A connection to the RPi is open right now (its newest status may still be old: see hv_view())."""
        return self.client is not None

    def view(self):
        config = self.store.config
        self.local_addresses()  # refreshed here, not while holding the lock
        with self.lock:
            connected, hello, status = self.client is not None, self.hello, self.status
            seconds = None if self.status_mono is None else time.monotonic() - self.status_mono
            age = None if seconds is None else round(seconds, 1)
            return {'connected': connected, 'host': config['rpi_host'], 'port': config['rpi_port'],
                    'error': self.error, 'server': hello if connected else None,
                    'pending': self.pending, 'status': status, 'status_age_s': age,
                    'hv': hv_state(connected, status, seconds), 'flow': flow_state(connected, hello, status),
                    'parameters': params_state(status if seconds is not None and seconds <= 10.0 else None),
                    'own_job': self.own_job, 'found': self.found if not connected else [],
                    'addresses': self.addresses}

    # ---- command
    def command(self, cmd, attended=True, claim=False, **fields):
        """Send one command. ``attended`` False is for a scheduled discharge: nobody is expected to
        watch the screen, so that run is not stopped when the screen goes away.

        ``claim`` True: the caller saves the job's result itself. The job is marked as handled before
        the connection is free again, so this connection's own thread never fetches, saves and
        reports it a second time."""
        with self.lock:
            self.pending = cmd
        try:
            with self.request_lock:
                if self.client is None:
                    raise RuntimeError('Not connected yet RPi: ' + self.error)
                try:
                    reply = self.client.request(cmd, **fields)
                except (ConnectionError, OSError) as exc:
                    self._drop(str(exc))
                    raise RuntimeError('The command did not receive a reply (the connection was interrupted); please confirm the device status and try again.') from exc
                if claim and reply.get('job'):
                    with self.lock:
                        (self.claimed if self.handled is None else self.handled).add(reply['job'])
            if cmd == 'plasma_run' and attended:
                self.own_job = reply['job']
                self.ui_alive()
            return reply
        finally:
            with self.lock:
                self.pending = ''
            self.wake.set()

    def rpi_ready(self):
        """The status when a discharge may start right now; RuntimeError says why not."""
        status = self.latest()
        if status is None:
            raise RuntimeError('and RPi Not connected')
        if status.get('busy'):
            raise RuntimeError('RPi is performing other work')
        ready = status.get('ready') or {}
        if not ready.get('plasma'):
            reasons = '; '.join(rewrite_reason(problem['reason']) for problem in ready.get('problems', []))
            raise RuntimeError('The pre-discharge inspection failed:' + (reasons or 'The device status has not been read yet'))
        return status

    def wait_job(self, job_id, stop_event=None, limit_s=360, lost_s=30):
        """(status, note) once the RPi reports how that job ended: 'done', 'stopped' or 'failed'.

        ``limit_s``: how long to wait for the end (the RPi's own limit for a discharge is 215 s plus
        60 s for switching off). ``lost_s``: how long the connection may be gone before the job
        counts as lost. ``stop_event``: once it is set (STOP was pressed) the job gets
        STOP_WAIT_SECONDS more to end; this call never stops anything itself.
        """
        deadline = time.monotonic() + limit_s
        lost = None
        stop_seen = False
        while time.monotonic() < deadline:
            if stop_event is not None and not stop_seen and stop_event.is_set():
                stop_seen = True
                if time.monotonic() + STOP_WAIT_SECONDS < deadline:
                    deadline, limit_s = time.monotonic() + STOP_WAIT_SECONDS, STOP_WAIT_SECONDS
            status = self.latest(max_age=5.0)
            if status is None:
                lost = lost or time.monotonic()
                if time.monotonic() - lost > lost_s:
                    return 'failed', 'and RPi ’s connection is interrupted and the results of this measurement cannot be confirmed; please go to the site to confirm the equipment'
            else:
                lost = None
                job = status.get('job')
                if job and job['id'] == job_id and job['state'] != 'running':
                    if job['state'] == 'done':
                        return 'done', ''
                    note = ' '.join(filter(None, (CANCEL_REASONS.get(job.get('cancel_reason'), ''), job.get('error'))))
                    post = job.get('post_stop')
                    if post and not post.get('ok'):
                        note += f'; after stopping OFF readback unconfirmed ({post.get("error")}) — Please confirm physically that the high voltage has been turned off'
                    stopped = job['state'] == 'cancelled' and job.get('cancel_reason') == 'stop'
                    return ('stopped' if stopped else 'failed'), note
            time.sleep(.2)
        return 'failed', f'exceeds {limit_s:.0f} seconds have not ended yet; please go to the site to confirm the device'

    def fetch_result(self, job_id):
        """The spectra of a finished job (the RPi's "result" reply, with every frame).

        Only the read is tried again, once, after RESULT_RETRY_SECONDS: a discharge is never repeated.
        """
        try:
            return self.command('result', job=job_id, frames=True)
        except (RuntimeError, PlasmicError):
            time.sleep(RESULT_RETRY_SECONDS)
        return self.command('result', job=job_id, frames=True)

    def stop(self):
        """Always over a fresh connection, so STOP never waits behind another command."""
        config = self.store.config
        try:
            reply = plasmic_client.stop(config['rpi_host'], config['rpi_port'])
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'The stop command cannot be sent RPi ({exc}) — Please physically turn off the high voltage immediately') from exc
        finally:
            self.wake.set()
        if reply.get('ok') is not True:
            raise RuntimeError('RPi Unconfirmed stop:' + str(reply.get('error')) + ' — Please confirm physically that the high voltage has been turned off')
        return reply

    def close(self):
        self.closed.set()
        self.wake.set()
        with self.lock:
            client, self.client = self.client, None
        if client is not None:
            client.close()


Link = RpiLink  # the name this class had in app.py
