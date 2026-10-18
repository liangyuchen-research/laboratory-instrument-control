"""A pretend RS485 bus with the Nano and the HSPY on it, a pretend trigger pin and a pretend
spectrometer program: enough to run the RPi discharge tools on any computer, with no equipment.

  * The Nano is a line-by-line port of nano/source_split/nano_rs485_v2_pulse/ControlCore.h and of
    the register map in nano_rs485_v2_pulse.ino (firmware 1.2.1): sessions, sequence numbers, the
    2 s lease, ARM/START, the D12 trigger and the pulse train on D13, timed on its 100 us grid.
  * Bytes take as long as at 9600 baud, the Nano waits its 5 ms frame gap, and the HSPY does not
    answer a request sent within 10 ms of a Nano reply (measured on site, see plasma_job.py).
  * The spectrometer program (simulated_spectrometer.py, started in place of hardware_verify.py) keeps
    the same files, marks and times, and reports how much of the Nano's pulses fell inside each
    exposure.

The tools talk to all this through their own, unchanged code (nano_verify.Client,
HSPY_Read_Status.read_status, plasma_job.Trigger, plasma_job.start_spectrum ...). Nothing here
touches a real serial port, GPIO or network.

What is assumed here because it cannot be known without the equipment (a green run proves nothing
about these; the first real runs have to show them):

  * Every reply byte is in the tool's hands the moment it has passed at 9600 baud. A USB-RS485
    adapter hands bytes over later (Rig(latency=...) adds that; one timing test runs with it).
  * The Nano answers 5.2 ms after a request. The real one also writes one LCD character per pass
    of its main loop when the bus has been quiet for 20 ms, which can add a few ms.
  * The Nano's clock is exact (Rig(nano_rate=...) makes it fast or slow; a resonator is off by a
    few tenths of a percent).
  * A write to the trigger pin reaches the Nano at once and reads back what was written; the real
    pin sits behind an I2C expander. A broken wire is `rig.nano.d12 = lambda: False`.
  * The supply answers in 4 ms, ramps in a straight line and never loses a reply by itself.
  * The spectrometer program is ended the POSIX way: SIGTERM is a request that the program acts on
    only between two SDK calls (see simulated_spectrometer.py), SIGKILL ends it at once.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import types
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'rpi' / 'rpi_tools'
APP = ROOT / 'rpi' / 'app'
BYTE_SECONDS = 10 / 9600
FRAME_GAP_SECONDS = .005  # FRAME_GAP_US of the sketch: what the Nano hears with less silence between is one frame
LOCK_BUSY = 75            # the exit code of "flock -n -E 75" when another program holds the spectrometer
if not hasattr(signal, 'SIGKILL'):
    signal.SIGKILL = 9    # Windows has none; the tools name it, and Rig.killpg must tell it from SIGTERM
# Not time.monotonic: on Windows (Python 3.12) that one moves in 16 ms steps, and a 10 ms trigger
# pulse could begin and end between two of the pretend Nano's clock readings.
clock = time.perf_counter

IDLE, ARMED, WAIT_TRIGGER, RUNNING, DONE, FAULT = range(6)
NO_FAULT, HEARTBEAT_LOST, TRIGGER_TIMEOUT, JOB_TIMEOUT = range(4)
OPEN_SESSION, HEARTBEAT, ARM, START, STOP, CLEAR_FAULT, POWER_REPORT = range(1, 8)
OK, CONFIG_APPLIED, STOPPED = 0, 1, 2
(BAD_SESSION, BAD_SEQUENCE, SEQUENCE_CONFLICT, BAD_STATE, BAD_CONFIG, REVISION_MISMATCH,
 OUTPUT_INHIBITED, BAD_COMMAND, LEASE_EXPIRED, BAD_JOB) = range(10, 20)
NO_PHASE, HIGH_PHASE, LOW_PHASE = range(3)
TICK_US, LEASE_US = 100, 2_000_000
TRIGGER_WAIT_US, JOB_LIMIT_US, POWER_TTL_US = 30_000_000, 120_000_000, 5_000_000
CONFIG_WORDS, STATUS_WORDS = 18, 40


def crc16(data):
    crc = 0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xa001 if crc & 1 else 0)
    return crc


def framed(body):
    return bytes(body) + struct.pack('<H', crc16(body))


class Config:
    FIELDS = ('revision', 'onUs', 'offUs', 'cycles', 'loops', 'integrationUs', 'intervalUs', 'voltageV', 'crc')

    def __init__(self, data=bytes(36)):
        for name, value in zip(self.FIELDS, struct.unpack('>9I', data)):
            setattr(self, name, value)

    def encode(self):
        return struct.pack('>9I', *(getattr(self, name) for name in self.FIELDS))

    def valid(self, data):
        if not self.revision or zlib.crc32(data[:32]) != self.crc:
            return False
        if self.onUs < 1000 or self.onUs > 1000000 or self.offUs < 1000 or self.offUs > 1000000:
            return False
        if self.onUs % TICK_US or self.offUs % TICK_US or not self.cycles or not self.loops:
            return False
        if self.cycles > 10000 or self.loops > 1000 or self.cycles * self.loops > 10000:
            return False
        if (self.onUs + self.offUs) * self.cycles * self.loops > JOB_LIMIT_US:
            return False
        if not self.integrationUs or self.integrationUs > 60000000 or self.intervalUs < self.integrationUs:
            return False
        return not (self.intervalUs > 60000000 or self.voltageV > 1000)


class Core:
    """plasmic::Controller of ControlCore.h; the names are the firmware's, to compare side by side."""

    def __init__(self, allow=True):
        self.config = Config()
        self.state, self.fault, self.phase, self.result = IDLE, NO_FAULT, NO_PHASE, OK
        self.configValid = self.outputHigh = self.trigger = self.seenLow = self.triggerSeen = self.leaseValid = False
        self.outputsAllowed = allow
        self.session = self.lastSeq = self.job = self.pulses = self.loopsDone = self.aborted = 0
        self.statusSeq = self.lastEdgeUs = 0
        self.heartbeatUs = self.jobStartUs = self.waitStartUs = self.edgeStartUs = 0
        self.powerReport, self.powerFresh, self.powerReportUs, self.powerRequest = 0, False, 0, 0
        self.cycleDone = 0
        self.lastPayload, self.lastKind, self.lastLength, self.cachedResult = b'', 0, 0, OK

    def active(self):
        return self.state in (ARMED, WAIT_TRIGGER, RUNNING)

    def fresh(self, now):
        return bool(self.session and self.leaseValid and now - self.heartbeatUs < LEASE_US)

    def displayedPower(self, now):
        return self.powerReport if self.powerFresh and now - self.powerReportUs < POWER_TTL_US else 0

    def stop(self, now):
        if self.outputHigh:
            self.aborted += 1
            self.lastEdgeUs = now
        self.outputHigh, self.phase, self.seenLow = False, NO_PHASE, False
        if self.state != FAULT:
            self.state = IDLE
        self.statusSeq += 1

    def fail(self, fault, now):
        self.stop(now)
        self.fault, self.state = fault, FAULT
        self.statusSeq += 1

    def tick(self, now, level):
        self.trigger = level
        if self.powerFresh and now - self.powerReportUs >= POWER_TTL_US:
            self.powerFresh = False
        if self.leaseValid and now - self.heartbeatUs >= LEASE_US:
            self.leaseValid = False
        if not self.active():
            return
        if not self.fresh(now):
            return self.fail(HEARTBEAT_LOST, now)
        if self.state == ARMED:
            return
        if now - self.jobStartUs >= JOB_LIMIT_US:
            return self.fail(JOB_TIMEOUT, now)
        if self.state == WAIT_TRIGGER:
            if now - self.waitStartUs >= TRIGGER_WAIT_US:
                return self.fail(TRIGGER_TIMEOUT, now)
            if not level:
                self.seenLow = True
            if level and self.seenLow:
                self.seenLow, self.triggerSeen, self.outputHigh = False, True, self.outputsAllowed
                self.phase, self.state = HIGH_PHASE, RUNNING
                self.edgeStartUs = self.lastEdgeUs = now
                self.statusSeq += 1
            return
        if self.phase == HIGH_PHASE and now - self.edgeStartUs >= self.config.onUs:
            self.outputHigh = False
            self.pulses += 1
            self.phase = LOW_PHASE
            self.edgeStartUs = self.lastEdgeUs = now
            self.statusSeq += 1
        elif self.phase == LOW_PHASE and now - self.edgeStartUs >= self.config.offUs:
            self.cycleDone += 1
            if self.cycleDone >= self.config.cycles:
                self.cycleDone = 0
                self.loopsDone += 1
                self.phase = NO_PHASE
                if self.loopsDone >= self.config.loops:
                    self.state = DONE
                else:
                    self.state, self.waitStartUs, self.seenLow = WAIT_TRIGGER, now, False
                self.statusSeq += 1
            else:
                self.outputHigh, self.phase = self.outputsAllowed, HIGH_PHASE
                self.edgeStartUs = self.lastEdgeUs = now
                self.statusSeq += 1

    def setConfig(self, payload, candidate, candidate_valid):
        if not self.checkSequence(payload, 44, 1):
            return self.result
        result = CONFIG_APPLIED
        if self.state not in (IDLE, DONE):
            result = BAD_STATE
        elif not candidate_valid:
            result = BAD_CONFIG
        elif self.configValid and candidate.revision <= self.config.revision:
            result = REVISION_MISMATCH
        else:
            self.config, self.configValid, self.state = candidate, True, IDLE
            self.statusSeq += 1
        return self.remember(payload, 44, 1, result)

    def command(self, payload, now):
        session, sequence, op, job, revision = struct.unpack('>IIHII', payload[:18])
        if op == STOP:
            self.stop(now)
            self.result = STOPPED
            return STOPPED
        if op == OPEN_SESSION and session and sequence and session != self.session:
            if self.active():
                self.result = BAD_STATE
                return BAD_STATE
            if job or revision:
                self.result = BAD_COMMAND
                return BAD_COMMAND
            self.session, self.lastSeq, self.lastLength, self.configValid = session, 0, 0, False
            self.config = Config()
            self.heartbeatUs, self.leaseValid, self.job = now, True, 0
            self.powerReport, self.powerFresh, self.powerReportUs, self.powerRequest = 0, False, 0, 0
            if self.state != FAULT:
                self.state = IDLE
            self.statusSeq += 1
        if not self.checkSequence(payload, 18, 2):
            return self.result
        result = OK
        if op == OPEN_SESSION:
            if self.lastSeq or job or revision:
                result = BAD_COMMAND
        elif op == HEARTBEAT:
            if job or revision:
                result = BAD_COMMAND
            else:
                self.heartbeatUs, self.leaseValid = now, True
        elif op == POWER_REPORT:
            if job > 2 or revision:
                result = BAD_COMMAND
            else:
                self.powerReport, self.powerFresh = job, job != 0
                self.powerReportUs, self.powerRequest = now, sequence
                self.statusSeq += 1
        elif op == ARM:
            if not self.outputsAllowed:
                result = OUTPUT_INHIBITED
            elif self.state not in (IDLE, DONE):
                result = BAD_STATE
            elif not self.configValid or revision != self.config.revision:
                result = REVISION_MISMATCH
            elif not job:
                result = BAD_JOB
            elif not self.fresh(now):
                result = LEASE_EXPIRED
            else:
                self.state, self.job = ARMED, job
                self.pulses = self.loopsDone = self.aborted = self.cycleDone = 0
                self.triggerSeen = False
                self.statusSeq += 1
        elif op == START:
            if not self.outputsAllowed:
                result = OUTPUT_INHIBITED
            elif self.state != ARMED:
                result = BAD_STATE
            elif not job or job != self.job:
                result = BAD_JOB
            elif revision != self.config.revision:
                result = REVISION_MISMATCH
            elif not self.fresh(now):
                result = LEASE_EXPIRED
            else:
                self.state, self.seenLow = WAIT_TRIGGER, False
                self.jobStartUs = self.waitStartUs = now
                self.statusSeq += 1
        elif op == CLEAR_FAULT:
            if self.active() or job or revision:
                result = BAD_STATE
            elif not self.fresh(now):
                result = LEASE_EXPIRED
            else:
                self.stop(now)
                self.fault, self.state = NO_FAULT, IDLE
                self.statusSeq += 1
        else:
            result = BAD_COMMAND
        return self.remember(payload, 18, 2, result)

    def checkSequence(self, payload, length, kind):
        session, sequence = struct.unpack('>II', payload[:8])
        if not self.session or session != self.session:
            self.result = BAD_SESSION
            return False
        if not sequence or sequence < self.lastSeq:
            self.result = BAD_SEQUENCE
            return False
        if sequence == self.lastSeq:
            same = kind == self.lastKind and length == self.lastLength and payload[:length] == self.lastPayload
            self.result = self.cachedResult if same else SEQUENCE_CONFLICT
            return False
        return True

    def remember(self, payload, length, kind, result):
        self.lastPayload, self.lastLength, self.lastKind = bytes(payload[:length]), length, kind
        self.lastSeq = struct.unpack('>I', payload[4:8])[0]
        self.cachedResult = self.result = result
        return result


class Nano:
    """The firmware around the core: Modbus frames in and out, the timer tick, D12 in, D13 out."""
    ADDRESS = 2

    def __init__(self, d12, pulse_log=None, rate=1.0, wall=None):
        self.core = Core()
        self.d12 = d12                  # callable: is the trigger pin HIGH right now?
        self.pulse_log = pulse_log      # file: one line per D13 edge, for the spectrometer program
        self.rate = rate                # the Nano's seconds per real second (its resonator is not exact)
        self.lock = threading.RLock()
        self.start = clock()
        # time of day = clock() + wall. The rig hands the same number to the pretend spectrometer
        # program, so that its times and these compare to the microsecond (see Rig.__init__).
        self.wall = time.time() - clock() if wall is None else wall
        self.last_tick = 0
        self.level = False
        self.commands = []              # (seconds, op or 'config', result)
        self.trains = []                # seconds at which each pulse train began
        self.renewals = []              # seconds at which the lease was renewed
        self.longest_gap = 0.0          # longest time between two renewals while a job was active
        self.lease_at = None
        self.resets = 0
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def micros(self):
        return int((clock() - self.start) * self.rate * 1e6)

    def seconds(self, micros):
        """Real seconds since the rig was made, for a time on the Nano's own clock."""
        return micros / 1e6 / self.rate

    def reset(self):
        """What a power dip or the watchdog does: the firmware starts again, D13 LOW, with no
        session, no parameters and no job. (Its clock is left running here; nothing looks at it.)"""
        with self.lock:
            if self.core.outputHigh:
                self._edge(self.micros(), False, False)
            self.core = Core()
            self.lease_at = None
            self.resets += 1

    def _run(self):
        while not self.closed.is_set():
            self.advance()
            time.sleep(.0005)  # not Event.wait: on Windows that wakes only every 15 ms

    def advance(self):
        """Every 100 us tick that is due. The pin tells the Nano when it changes (Rig.set_pin), so the
        last of these ticks is the one that sees a new level.

        The pin is read under the lock Rig.set_pin holds while it changes it. Read outside, this
        thread could go on with a HIGH the tool had lowered meanwhile, and one D12 pulse started a
        second pulse train whenever a train was no longer than the pulse: the firmware cannot do that.
        """
        with self.lock:
            level = self.d12()
            now = self.micros()
            if now - self.last_tick > 200_000 and not self.core.active():
                self.last_tick = now - now % TICK_US - TICK_US  # nothing happens while idle: skip ahead
            while self.last_tick + TICK_US <= now:
                self.last_tick += TICK_US
                last = self.last_tick + TICK_US > now
                was_high, was_state = self.core.outputHigh, self.core.state
                self.core.tick(self.last_tick, level if last else self.level)
                if self.core.outputHigh != was_high:
                    self._edge(self.last_tick, self.core.outputHigh, was_state == WAIT_TRIGGER)
                if self.core.active() and self.lease_at is not None:
                    self.longest_gap = max(self.longest_gap, (self.last_tick - self.lease_at) / 1e6)
            self.level = level

    def _edge(self, micros, high, first):
        if high and first:
            self.trains.append(self.seconds(micros))
        if self.pulse_log is not None:
            self.pulse_log.write('%s %.6f\n' % ('R' if high else 'F', self.start + self.seconds(micros) + self.wall))
            self.pulse_log.flush()

    def status_words(self, now):
        core = self.core
        words = [0] * STATUS_WORDS
        words[0], words[1], words[2] = 0x504e, 1, 0x0121
        words[3] = (1 | (2 if core.configValid else 0) | (4 if core.session else 0)
                    | (8 if core.fresh(now) else 0) | (16 if core.triggerSeen else 0))
        words[4], words[5], words[6] = core.state, core.fault, int(core.outputHigh)
        words[7], words[8], words[9] = int(core.outputHigh), int(self.level), core.result
        values = [core.session, core.lastSeq, core.config.revision, core.config.crc, core.job, core.pulses,
                  core.loopsDone, now // 1000,
                  (now - core.heartbeatUs) // 1000 if core.session else 0xffffffff,
                  (now - core.jobStartUs) // 1000 if core.state in (WAIT_TRIGGER, RUNNING) else 0,
                  core.statusSeq]
        for index, value in enumerate(values):
            words[10 + index * 2], words[11 + index * 2] = (value >> 16) & 0xffff, value & 0xffff
        words[32], words[33] = 0, core.phase
        words[34], words[35] = (core.aborted >> 16) & 0xffff, core.aborted & 0xffff
        words[36], words[37] = (core.lastEdgeUs >> 16) & 0xffff, core.lastEdgeUs & 0xffff
        words[38], words[39] = TICK_US, int(core.state == RUNNING)
        return words

    def handle(self, frame):
        """One received frame; the reply, or None when the firmware stays silent."""
        if len(frame) < 4 or frame[0] != self.ADDRESS or crc16(frame):
            return None
        self.advance()
        with self.lock:
            core, now = self.core, self.micros()
            function = frame[1]

            def exception(code):
                return framed(bytes([self.ADDRESS, function | 0x80, code]))
            if function == 3:
                if len(frame) != 8:
                    return exception(3)
                start, count = struct.unpack('>HH', frame[2:6])
                if not count or count > STATUS_WORDS:
                    return exception(3)
                if start < STATUS_WORDS and start + count <= STATUS_WORDS:
                    words = self.status_words(now)[start:start + count]
                elif start >= 0x100 and start + count <= 0x100 + CONFIG_WORDS:
                    words = list(struct.unpack('>18H', core.config.encode()))[start - 0x100:start - 0x100 + count]
                elif start >= 0x300 and start + count <= 0x308:
                    age = (now - core.powerReportUs) // 1000 if core.powerFresh else 0xffffffff
                    block = [core.powerReport, core.displayedPower(now), (age >> 16) & 0xffff, age & 0xffff,
                             (core.powerRequest >> 16) & 0xffff, core.powerRequest & 0xffff, int(core.active()), 1]
                    words = block[start - 0x300:start - 0x300 + count]
                else:
                    return exception(2)
                return framed(bytes([self.ADDRESS, 3, count * 2]) + struct.pack('>%dH' % count, *words))
            if function != 16:
                return exception(1)
            if len(frame) < 9:
                return exception(3)
            start, count = struct.unpack('>HH', frame[2:6])
            if count > 22 or frame[6] != count * 2 or len(frame) != 9 + count * 2:
                return exception(3)
            payload = frame[7:7 + count * 2]
            if start == 0x100 and count == 22:
                candidate = Config(payload[8:44])
                result = core.setConfig(payload, candidate, candidate.valid(payload[8:44]))
                self.commands.append((self.seconds(now), 'config', result))
            elif start == 0x200 and count == 9:
                _session, _sequence, op = struct.unpack('>IIH', payload[:10])
                result = core.command(payload, now)
                self.commands.append((self.seconds(now), op, result))
                if result == OK and op in (OPEN_SESSION, HEARTBEAT):
                    self.renewals.append(self.seconds(now))
                    self.lease_at = now
            else:
                return exception(2)
            return framed(frame[:6])

    def close(self):
        self.closed.set()
        self.thread.join(2)


class Hspy:
    """The supply as the tools see it: five registers, a ramp after ON, zero shortly after OFF."""
    ADDRESS = 1

    def __init__(self, set_voltage=30, ramp_seconds=1.5, fall_seconds=.1, reach=1.0):
        self.set_voltage, self.set_current = set_voltage, 1000  # raw: 1000 = 0.1 A
        self.run_stop = 0
        self.ramp_seconds, self.fall_seconds = ramp_seconds, fall_seconds
        self.reach = reach              # below 1: the output never gets to the set value
        self.changed = clock() - 10
        self.changes = []               # (clock time, new Run/Stop) of every switching, for voltage_at()
        self.writes = []                # (seconds since the bus was made, register, value)
        self.start = clock()

    def voltage(self):
        return self.voltage_at(clock())

    def voltage_at(self, when):
        """The output at the clock time ``when`` (now or earlier), from the history of ON and OFF."""
        run_stop, changed = 0, self.start - 10
        for moment, value in self.changes:
            if moment > when:
                break
            run_stop, changed = value, moment
        elapsed = when - changed
        if run_stop:
            return int(self.set_voltage * self.reach * min(1.0, elapsed / self.ramp_seconds))
        return 0 if elapsed >= self.fall_seconds else int(self.set_voltage * (1 - elapsed / self.fall_seconds))

    def handle(self, frame):
        if len(frame) != 8 or frame[0] != self.ADDRESS or crc16(frame):
            return None
        function, register, value = struct.unpack('>BHH', frame[1:6])
        if function == 3 and (register, value) == (0, 5):
            return framed(bytes([self.ADDRESS, 3, 10]) + struct.pack(
                '>5H', self.set_voltage, self.set_current, self.voltage(), 20 if self.run_stop else 0, self.run_stop))
        if function == 6 and register in (0, 4):
            self.writes.append((clock() - self.start, register, value))
            if register == 4 and value in (0, 1) and value != self.run_stop:
                self.run_stop, self.changed = value, clock()
                self.changes.append((self.changed, value))
            elif register == 0:
                self.set_voltage = value
            return bytes(frame)
        return framed(bytes([self.ADDRESS, function | 0x80, 1]))


class Bus:
    """What pyserial would be: write a request, read the reply, byte by byte at 9600 baud."""

    def __init__(self, nano, hspy, latency=0.0):
        self.nano, self.hspy = nano, hspy
        self.latency = latency          # how much later than on the wire the adapter hands a byte over
        self.timeout = self.write_timeout = 1.0
        self.pending = []               # (when it has arrived, byte)
        self.nano_spoke = float('-inf')  # when the Nano's last reply ended
        self.last_byte = float('-inf')  # when the last byte of anybody was on the wire
        self.exchanges = 0
        self.damage_nano_replies = 0    # so many of the Nano's next replies arrive with one byte wrong

    def reset_input_buffer(self):
        now = clock()
        self.pending = [item for item in self.pending if item[0] > now]

    @property
    def in_waiting(self):
        now = clock()
        return sum(1 for when, _byte in self.pending if when <= now)

    def write(self, data):
        data = bytes(data)
        sent = clock()
        # The Nano takes everything it hears, whoever sent it, for one frame until the line has
        # been silent for 5 ms: a request that follows other traffic sooner is lost in a bad frame.
        merged = sent - self.last_byte < FRAME_GAP_SECONDS
        time.sleep(len(data) * BYTE_SECONDS)
        self.last_byte = clock()
        self.exchanges += 1
        reply = None
        if data and data[0] == Nano.ADDRESS:
            time.sleep(.0052)           # FRAME_GAP_US, then 100 us for the driver to settle
            reply = None if merged else self.nano.handle(data)
            if reply and self.damage_nano_replies:
                self.damage_nano_replies -= 1
                reply = reply[:-1] + bytes([reply[-1] ^ 0x55])
        elif data and data[0] == Hspy.ADDRESS:
            time.sleep(.004)
            if sent - self.nano_spoke >= .010:
                reply = self.hspy.handle(data)
        if reply:
            begin = clock()
            self.pending += [(begin + (index + 1) * BYTE_SECONDS + self.latency, byte)
                             for index, byte in enumerate(reply)]
            self.last_byte = begin + len(reply) * BYTE_SECONDS
            if data[0] == Nano.ADDRESS:
                self.nano_spoke = self.last_byte
        return len(data)

    def read(self, size=1):
        deadline = clock() + (self.timeout or 0)
        while True:
            now = clock()
            arrived = [item for item in self.pending if item[0] <= now]
            if len(arrived) >= size or now >= deadline:
                taken = arrived[:size]
                self.pending = [item for item in self.pending if item not in taken]
                return bytes(byte for _when, byte in taken)
            waiting = sorted(when for when, _byte in self.pending if when > now)
            wanted = waiting[size - len(arrived) - 1] if len(waiting) >= size - len(arrived) else deadline
            time.sleep(max(0.0, min(wanted, deadline) - now))


class PinValue:
    """gpioN/value of the trigger pin. A write reaches the Nano at once, as over the wire; looking at
    a real file a thousand times a second missed pulses when Windows was busy."""

    def __init__(self, rig):
        self.rig = rig

    def write_text(self, text):
        self.rig.set_pin(text.strip() == '1')

    def read_text(self):
        return '1\n' if self.rig.pin_level else '0\n'


class Pin:
    """plasma_job.Trigger.pin: everything but "value" stays the folder Trigger found."""

    def __init__(self, rig, folder):
        self.value, self.folder = PinValue(rig), folder

    def __truediv__(self, name):
        return self.value if name == 'value' else self.folder / name


REAL_PROGRAM_ON_A_PRETEND_SDK = r'''"""Written into a test folder by tests/simulated_rig.py (Rig(real_program=True)).

Runs rpi_tools/hardware_verify.py as it is (copied beside this file as real_hardware_verify.py).
Only the vendor SDK is pretended (ctypes.CDLL), a SIGTERM is delivered as simulated_rig.Rig.killpg
leaves it, and on Windows a file that is being looked at is removed at the second try.
"""
import ctypes
import math
import os
import pathlib
import runpy
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
STOP = HERE / ('sigterm-%d' % os.getpid())
nap = time.sleep


def asked_to_stop():
    if STOP.exists():
        try:
            os.remove(str(STOP))       # one signal, one exception
        except OSError:
            pass
        raise RuntimeError('Interrupted by signal 15')


def sleep(seconds):
    """time.sleep for the program: its SIGTERM handler acts at once while Python waits."""
    end = time.perf_counter() + seconds
    while True:
        asked_to_stop()
        left = end - time.perf_counter()
        if left <= 0:
            return
        nap(min(.005, left))


class Function:
    def __init__(self, name):
        self.name, self.argtypes, self.restype = name, None, None

    def __call__(self, *args):
        name = self.name
        if name == 'UAI_SpectrometerOpen':
            nap(.08)
            args[1]._obj.value = 1
        elif name == 'UAI_SpectromoduleGetFrameSize':
            args[1]._obj.value = 1862
        elif name == 'UAI_SpectrometerWavelengthAcquire':
            for index in range(len(args[1])):
                args[1][index] = 180 + index * 320 / 1861
        elif name == 'UAI_SpectrometerDataOneshot':
            nap(args[1] / 1e6 + .012)  # nothing interrupts a call into the SDK
            for index in range(len(args[2])):
                args[2][index] = 700 + 30 * math.sin(index / 23)
        elif name in ('UAI_SpectrometerGetSerialNumber', 'UAI_SpectrometerGetModelName'):
            args[1].value = b'PRETEND'
        asked_to_stop()                # the handler runs as soon as the call is over
        return 0


class Library:
    def __getattr__(self, name):
        function = Function(name)
        setattr(self, name, function)
        return function


remove = pathlib.Path.unlink


def unlink(path, *args, **options):
    for attempt in range(400):
        try:
            return remove(path, *args, **options)
        except PermissionError:
            if attempt == 399:
                raise
            nap(.0005)


ctypes.CDLL = lambda path: Library()
time.sleep = sleep
pathlib.Path.unlink = unlink
sys.path.insert(0, @TOOLS@)            # nano_verify.py, which the program imports
sys.argv[0] = str(HERE / 'real_hardware_verify.py')
runpy.run_path(sys.argv[0], run_name='__main__')
'''.replace('@TOOLS@', repr(str(TOOLS)))


class Rig:
    """Everything a discharge tool needs, made of files in one temporary folder."""

    def __init__(self, folder, parameters, ramp_seconds=1.5, reach=1.0, latency=0.0, nano_rate=1.0,
                 real_program=False):
        self.folder = Path(folder)
        self.parameters = dict(parameters)
        # One time of day for the whole rig: clock() + wall. time.time() itself moves in steps of
        # about 1 ms on Windows (Python 3.12), too coarse to say whether an exposure opened before a
        # pulse that came 2 ms later; clock() is the same in every process of this computer.
        self.wall = time.time() - clock()
        self.app_dir = self.folder / 'app'
        self.tools_dir = self.folder / 'tools'      # only the spectrometer program is started from here
        self.output_dir = self.folder / 'job'
        self.app_dir.mkdir()
        self.tools_dir.mkdir()
        (self.app_dir / 'parameter.json').write_text(json.dumps(self.parameters), encoding='utf-8')
        if real_program:
            # rpi_tools/hardware_verify.py itself, unchanged; only the vendor SDK under it is pretended
            shutil.copy(TOOLS / 'hardware_verify.py', self.tools_dir / 'real_hardware_verify.py')
            (self.tools_dir / 'hardware_verify.py').write_text(REAL_PROGRAM_ON_A_PRETEND_SDK, encoding='utf-8')
        else:
            shutil.copy(Path(__file__).with_name('simulated_spectrometer.py'), self.tools_dir / 'hardware_verify.py')
        self.lock_held = False          # True: another program has the spectrometer, as flock would find
        self.signals = []               # (pid, signal) of every killpg the tool made
        self.spectrometer = self.tools_dir / 'spectrometer.json'  # what the pretend spectrometer should do
        self.set_spectrometer()
        gpio = self.folder / 'gpio'
        (gpio / 'gpiochip496').mkdir(parents=True)
        (gpio / 'gpiochip496' / 'label').write_text('1-0027\n')
        (gpio / 'gpiochip496' / 'ngpio').write_text('8\n')
        (gpio / 'gpiochip496' / 'base').write_text('496\n')
        self.pin = gpio / 'gpio499'
        self.pin.mkdir()
        (self.pin / 'value').write_text('0\n')
        (self.pin / 'active_low').write_text('0\n')
        (self.pin / 'direction').write_text('in\n')
        self.gpio = gpio
        self.pulse_log = (self.tools_dir / 'd13.log').open('w', encoding='ascii')
        self.pin_level = False
        self.nano = Nano(lambda: self.pin_level, self.pulse_log, nano_rate, wall=self.wall)
        self.hspy = Hspy(int(self.parameters['Voltage']), ramp_seconds, reach=reach)
        self.bus = Bus(self.nano, self.hspy, latency)
        self.children = {}
        self.started = []               # the command line of every spectrometer program that was started

    def train_times(self):
        """When each pulse train began, on the clock the spectrometer program reports in (time.time)."""
        return [self.nano.start + self.nano.wall + seconds for seconds in self.nano.trains]

    def voltage_seen_at(self, time_of_day):
        """The supply's output at a time of day the spectrometer program reported ("opened_at")."""
        return self.hspy.voltage_at(time_of_day - self.wall)

    def set_spectrometer(self, **wishes):
        """What the pretend spectrometer program should do; the wishes are listed in simulated_spectrometer.py.
        It is also told the rig's time of day ("wall"), so that both keep the same clock."""
        self.spectrometer.write_text(json.dumps(dict(wishes, wall=self.wall)), encoding='ascii')

    def set_pin(self, high):
        with self.nano.lock:            # the Nano's own thread never sees half of this (see Nano.advance)
            self.nano.advance()         # everything up to this moment happened at the old level
            self.pin_level = high
            self.nano.advance()

    def popen(self, args, **options):
        """In place of subprocess.Popen inside plasma_job: no flock, and remembered for killpg."""
        if args and args[0] == 'flock':
            args = args[5:]
            if self.lock_held:          # flock -n -E 75: it ends at once and the program is never started
                args = [sys.executable, '-c', 'raise SystemExit(%d)' % LOCK_BUSY]
        self.started.append([str(item) for item in args])
        child = subprocess.Popen(args, **options)
        self.children[child.pid] = child
        return child

    def killpg(self, pid, signum):
        """os.killpg as the spectrometer program meets it on the RPi.

        SIGTERM is only a request there: hardware_verify.py handles it in Python, and Python cannot
        run that handler while the program is inside an SDK call. So the request is left as a file
        the pretend program looks for between its calls. Only SIGKILL ends a program at once.
        """
        self.signals.append((pid, signum))
        child = self.children.get(pid)
        if child is None or child.poll() is not None:
            return
        if signum == signal.SIGKILL:
            child.kill()
        else:
            (self.tools_dir / ('sigterm-%d' % pid)).write_text(str(signum), encoding='ascii')

    def close(self):
        self.nano.close()
        self.pulse_log.close()
        for child in self.children.values():
            if child.poll() is None:
                child.kill()
            child.communicate()


def load_verified():
    """rpi_tools/plasma_job.py as it is; on Windows only the missing fcntl module is supplied."""
    if 'fcntl' not in sys.modules:
        try:
            import fcntl  # noqa: F401
        except ImportError:
            sys.modules['fcntl'] = types.ModuleType('fcntl')
    for folder in (str(APP), str(TOOLS)):
        if folder not in sys.path:
            sys.path.insert(0, folder)
    import plasma_job
    return plasma_job


class Session:
    """One tool run on a rig: the tool's own client, readers and trigger, bound to the pretend parts."""

    def __init__(self, rig):
        self.rig = rig
        self.verified = verified = load_verified()
        from HSPY_Read_Status import read_status
        self.saved = (verified.subprocess, getattr(os, 'killpg', None), verified.INTERRUPTS)
        verified.subprocess = types.SimpleNamespace(Popen=rig.popen, PIPE=subprocess.PIPE,
                                                    TimeoutExpired=subprocess.TimeoutExpired)
        os.killpg = rig.killpg
        verified.INTERRUPTS = verified.Interrupts()   # every run starts like a new process
        port = rig.bus
        self.nano = verified.GuardedClient(port)

        def power():
            with verified.INTERRUPTS.bus():
                verified.hspy_turnaround()
                return read_status(port, address=1, timeout=1.0)
        self.power = power
        self.power_write = lambda register, value: verified.checked_power_write(port, register, value)
        self.drain = lambda: verified.drain_bus(port)
        self.trigger = verified.Trigger(root=rig.gpio)   # finds the pin the way it does on the RPi
        self.trigger.pin = Pin(rig, self.trigger.pin)

    def arguments(self):
        rig = self.rig
        return (self.nano, self.power, self.power_write, self.trigger, rig.parameters,
                rig.app_dir, rig.tools_dir, rig.output_dir)

    def close(self):
        verified = self.verified
        verified.subprocess, killpg, verified.INTERRUPTS = self.saved
        if killpg is None:
            del os.killpg
        else:
            os.killpg = killpg
