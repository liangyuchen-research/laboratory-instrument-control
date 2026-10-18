#!/usr/bin/env python3
"""Plasmic Electrical Control - Extension of Pump Control Board Simulator: Autonomous Laboratory v7 emulator,plus mega/ ’s firmware has extra features.

V7 emulator (pc/vendor/v7/backend/simulator.py,(copied, cannot be changed) only recognized V7 original firmware. Pump control board burned out
mega/source/PlasmicMega , the practice mode needs to be the same as the real board, so here is the paragraph where the firmware is added
 (GP, GN, G: The board can check the weight of the scale by itself;K: The computer stops running without any end when there is no sound; CM, VL: Two pumps, pumps and solenoid valves for cleaning
can be set; Shutdown procedure forSTATE/READY of fw=, wd=, dose=, clean=, links=) Write it again according to the way of writing the firmware, with the same name and order. Python.**The firmware part has been changed (mega/tools/make_firmware.py) ,needs to be changed here.**

This is just a model: it allows the computer program to have something to talk to and test, but it cannot prove that the one burned into the board is correct.

    megasim.use(v7, link)      Let this one V7 Link Use the simulator here when starting (only affects this one)
"""
from __future__ import annotations

import statistics
import threading
import time

FW_TAG = 'p2'
READY = 'READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0,fw=' + FW_TAG
MAX_SPS = 8000
FIRST_MOTOR, LAST_MOTOR = 2, 9
# What hw_config.h (v7's) says, and so what the board holds after every restart; CM and VL change it until the next one.
GROUP_B_FIRST = 6
MOTOR_COUNT = LAST_MOTOR - FIRST_MOTOR + 1
NUM_VALVES = 2
CLEAN_WATER_MOTOR, CLEAN_OUT_MOTOR = 2, 8
MOTOR_VALVE_LIST = (1, 0, 0, 0, 0, 0, 0, 2)
(GD_IDLE, GD_BASELINE, GD_PRIMING, GD_COARSE, GD_APPROACH, GD_TRIM, GD_SETTLE, GD_FINAL, GD_TOPUP) = range(9)
PHASE_NAMES = ('idle', 'baseline', 'priming', 'coarse', 'approach', 'trim', 'settle', 'final', 'topup')
(GR_NONE, GR_DONE, GR_CANCELLED, GR_TIMEOUT, GR_NO_GAIN, GR_SCALE) = range(6)
RESULT_NAMES = ('none', 'done', 'cancelled', 'timeout', 'no_gain', 'scale')
PUMPING = (GD_PRIMING, GD_COARSE, GD_APPROACH, GD_TRIM, GD_TOPUP)

_classes = {}
_swap = threading.Lock()


def _integers(text, most):
    """Comma separated integers, as the firmware's doseParse: [] when one field is not an integer of at most
    nine digits, or when there are more than `most`."""
    out = []
    for field in text.split(','):
        field = field.strip()
        digits = field[1:] if field[:1] in ('+', '-') else field
        if not digits.isdigit() or len(digits) > 9 or len(out) >= most:
            return []
        out.append(int(field))
    return out


def simulator_class(base):
    """V7's SimulatedMega with what the firmware of mega/ adds. One class per base class."""
    if base in _classes:
        return _classes[base]

    class PlasmicMega(base):
        def __init__(self, cfg):
            super().__init__(cfg)
            # The two pumps of the cleaning and the valve of each motor are the board's own (CM, VL), not the PC's
            # settings file: V7's simulator reads them from its cfg, so this one keeps those two parts for itself.
            self.cfg = dict(cfg, cleaning=dict(cfg['cleaning']), valves=dict(cfg['valves']))
            self.scale_silent = False        # test seam: the weighing transmitter stops answering
            self._service_stop = threading.Event()
            self._service = None
            self.power_on()

        def power_on(self):
            """Everything the firmware of mega/ keeps in memory, as after a restart of the board."""
            self.scale_calibrated = False
            self._fail_count = 0
            self.cfg['cleaning'].update(water_motor=CLEAN_WATER_MOTOR, out_motor=CLEAN_OUT_MOTOR)
            self.cfg['valves']['motor_links'] = self._links(MOTOR_VALVE_LIST)
            self.wd_ms = 0
            self.wd_last_rx = time.monotonic()
            # GP, GN
            self.gp_set = False
            self.gd_sps = [0, 0, 0]
            self.gd_min_gain = 0.0
            self.gd_settle_s = self.gd_no_gain_s = self.gd_timeout_s = 0.0
            self.gd_sample_s = 4.0
            self.gd_base_n, self.gd_final_n = 5, 7
            # this dose
            self.gd_phase, self.gd_result, self.gd_motor = GD_IDLE, GR_NONE, -1
            self.gd_target = self.gd_lower = self.gd_slow_zone = self.gd_trim_zone = 0.0
            self.gd_baseline = self.gd_peak = self.gd_progress = self.gd_dispensed = self.gd_last_gain = 0.0
            self.gd_buf = []
            self.gd_topups, self.gd_stable = 0, True
            self.gd_started_at = self.gd_pump_started_at = self.gd_last_progress_at = 0.0
            self.gd_last_sample_at = self.gd_settle_until = self.gd_ended_at = 0.0

        # ---- life cycle ----------------------------------------------------------------------
        def start(self):
            super().start()                  # V7's banner, which _emit() completes with the firmware's name
            self._service = threading.Thread(target=self._service_loop, daemon=True, name='sim-plasmic')
            self._service.start()

        def stop(self):
            self._service_stop.set()
            super().stop()
            if self._service is not None:
                self._service.join(timeout=1.0)

        def _service_loop(self):
            """What loop() does after serviceMotors(): serviceDose() and serviceWatchdog()."""
            while not self._service_stop.wait(0.01):
                with self._lock:
                    self._service_dose()
                    self._service_watchdog()

        # ---- what the board says ---------------------------------------------------------------
        def _emit(self, text):
            if text is None:                 # (_weight_line() below has sent its line itself)
                return
            if text.startswith('READY,') and ',fw=' not in text:
                text += ',fw=' + FW_TAG      # the banner of this firmware
            super()._emit(text)

        @staticmethod
        def _links(valves):
            """MOTOR_VALVE[] as V7's simulator wants it: one row per motor that has a valve."""
            return [{'motor': FIRST_MOTOR + index, 'valve': valve} for index, valve in enumerate(valves) if valve]

        def _motor_valves(self):
            """MOTOR_VALVE[]: the valve of motor 2, 3, … 9 (0 = none)."""
            links = {int(link['motor']): int(link['valve']) for link in self.cfg['valves']['motor_links']}
            return [links.get(motor, 0) for motor in range(FIRST_MOTOR, LAST_MOTOR + 1)]

        def _state_line(self):
            clean = self.cfg['cleaning']
            return '%s,fw=%s,wd=%d,dose=%d,clean=%d:%d,links=%s' % (
                super()._state_line(), FW_TAG, self.wd_ms, 1 if self.gd_phase != GD_IDLE else 0,
                int(clean['water_motor']), int(clean['out_motor']), ''.join(str(valve) for valve in self._motor_valves()))

        def _weight_line(self):
            """scaleReport(): the WEIGHT line first, then the dose sees the reading; three failed readings in a
            row stop the stream (and a dose). Sends its lines itself, in the firmware's order: returns None."""
            if self.scale_silent:
                self._emit('ERR,timeout(module did not respond)')
                if self.stream_hz > 0:
                    self._fail_count += 1
                    if self._fail_count >= 3:
                        self.stream_hz, self._fail_count = 0, 0
                        self._emit('ERR,stream_stopped_no_reply')
                        self._dose_end(GR_SCALE)         # (does nothing when no dose is running)
                return None
            self._fail_count = 0
            line = super()._weight_line()
            self._emit(line)
            parts = line.split(',')
            self._dose_sample(float(parts[1]), parts[5].strip() == '1')
            return None

        # ---- commands ----------------------------------------------------------------------
        def _handle(self, line):
            try:
                if self._plasmic(line.strip().upper()):
                    return None
                return super()._handle(line)
            finally:
                with self._lock:
                    self.wd_last_rx = time.monotonic()   # after the command, as loop() does

        def _plasmic(self, up):
            """The part of handleCommand() that comes before V7's own. True: answered here."""
            with self._lock:
                if self.gd_phase != GD_IDLE:
                    if not self._dose_allows(up):
                        self._emit('ERR,dose_active')
                        return True
                    if up.startswith('Y,'):              # only Y,STOP gets here: sent, not waited for
                        self._emit('OK,pump_stop_sent')
                        return True
                if up.startswith(('G,', 'GP,', 'GN,')):
                    key, _, rest = up.partition(',')
                    self._handle_dose(key, rest.strip())
                    return True
                if up.startswith('K,'):
                    value = _integers(up[2:], 1)
                    if not value or (value[0] != 0 and not 2000 <= value[0] <= 60000):
                        self._emit('ERR,arg')
                        return True
                    self.wd_ms = value[0]
                    self._emit('OK,watchdog_ms=%d' % self.wd_ms)
                    return True
                if up.startswith(('CM,', 'VL,')):
                    key, _, rest = up.partition(',')
                    self._handle_setup(key, rest.strip())
                    return True
                # What V7's own handlers do first when a dose is running.
                if up in ('X', 'STOP'):
                    self._dose_finish(GR_CANCELLED)      # (nothing when no dose is running; no STATE here)
                elif up.startswith('V,') and self.gd_phase != GD_IDLE:
                    parts = up.split(',')                # (speed 0, or it would not have been let through)
                    try:
                        same = len(parts) == 3 and int(parts[1]) == self.gd_motor
                    except ValueError:
                        same = False
                    if same:
                        self._dose_end(GR_CANCELLED)
                elif up.startswith(('W,DIV,', 'W,CAL,')):
                    try:
                        value = float(up.split(',')[2])
                    except (IndexError, ValueError):
                        value = 0.0
                    if (up.startswith('W,DIV,') and value != 0) or (up.startswith('W,CAL,') and value > 0):
                        self.scale_calibrated = True     # as the firmware: grams mean something from here on
            return False

        def _dose_allows(self, up):
            if up in ('?', 'X', 'STOP', 'G,?', 'Y,STOP', 'W,READ', 'W,CFG') or up.startswith('K,'):
                return True
            if up.startswith('V,'):                      # speed 0 = stop, of any motor
                try:
                    return up.count(',') >= 2 and float(up.rsplit(',', 1)[1]) == 0
                except ValueError:
                    return False                         # (the firmware reads text that is no number as 0)
            return False

        def _handle_setup(self, key, rest):
            """handleSetupCommand(): CM and VL store numbers; nothing moves."""
            if self.cleaning:
                self._emit('ERR,cleaning_active')
                return
            if key == 'CM':                              # water, OUT: one on each driver (they turn together)
                v = _integers(rest, 2)
                if (len(v) != 2 or not all(FIRST_MOTOR <= motor <= LAST_MOTOR for motor in v)
                        or (v[0] < GROUP_B_FIRST) == (v[1] < GROUP_B_FIRST)):
                    self._emit('ERR,arg')
                    return
                self.cfg['cleaning'].update(water_motor=v[0], out_motor=v[1])
                self._emit('OK,clean=%d:%d' % (v[0], v[1]))
                return
            v = _integers(rest, MOTOR_COUNT)             # the valve of motor 2 … 9; a valve belongs to one motor at most
            if (len(v) != MOTOR_COUNT or not all(0 <= valve <= NUM_VALVES for valve in v)
                    or any(v.count(valve) > 1 for valve in range(1, NUM_VALVES + 1))):
                self._emit('ERR,arg')
                return
            if any(state != 'idle' for state in self.mstate.values()) or any(self._pending_start.values()):
                self._emit('ERR,busy')
                return
            if any(self.valves.values()) or any(at is not None for at in self._valve_close_at.values()):
                self._emit('ERR,busy')                   # a valve that is open would have nobody to close it
                return
            self.cfg['valves']['motor_links'] = self._links(v)
            self._emit('OK,links=' + ''.join(str(valve) for valve in v))

        def _handle_dose(self, key, rest):
            if key == 'G' and rest == '?':
                self._emit(self._dose_status())
                return
            if key == 'GP':                              # fast, approach, trim sps; minimum gain mg; settle, no gain, limit ms
                v = _integers(rest, 7)
                if (len(v) != 7 or v[2] < 1 or v[1] < v[2] or v[0] < v[1] or v[0] > MAX_SPS
                        or not 1 <= v[3] <= 100000 or not 0 <= v[4] <= 60000
                        or not 2000 <= v[5] <= 600000 or not 2000 <= v[6] <= 1800000):
                    self._emit('ERR,arg')
                    return
                self.gd_sps = [v[0], v[1], v[2]]
                self.gd_min_gain = v[3] / 1000.0
                self.gd_settle_s, self.gd_no_gain_s, self.gd_timeout_s = v[4] / 1000.0, v[5] / 1000.0, v[6] / 1000.0
                self.gp_set = True
                self._emit('OK,dose_profile')
                return
            if key == 'GN':                              # baseline readings, final readings, ms without a reading
                v = _integers(rest, 3)
                if len(v) != 3 or not 1 <= v[0] <= 9 or not 1 <= v[1] <= 9 or not 500 <= v[2] <= 30000:
                    self._emit('ERR,arg')
                    return
                self.gd_base_n, self.gd_final_n, self.gd_sample_s = v[0], v[1], v[2] / 1000.0
                self._emit('OK,dose_samples')
                return
            v = _integers(rest, 5)                       # motor, target mg, slow zone mg, trim zone mg, tolerance mg
            if (key != 'G' or len(v) != 5 or not FIRST_MOTOR <= v[0] <= LAST_MOTOR or not 10 <= v[1] <= 1000000
                    or v[3] < 0 or v[2] < v[3] or v[2] > 1000000 or v[4] < 0 or v[4] >= v[1]):
                self._emit('ERR,arg')
                return
            if not self.gp_set:
                self._emit('ERR,dose_profile')
                return
            if self.cleaning:
                self._emit('ERR,cleaning_active')
                return
            if any(state != 'idle' for state in self.mstate.values()) or any(self._pending_start.values()):
                self._emit('ERR,busy')
                return
            if not self.scale_calibrated:
                self._emit('ERR,scale_uncalibrated')
                return
            if self.scale_silent:
                self._emit('ERR,scale_timeout')
                return
            now = time.monotonic()
            self.gd_motor = v[0]
            self.gd_target = v[1] / 1000.0
            self.gd_slow_zone, self.gd_trim_zone = v[2] / 1000.0, v[3] / 1000.0
            self.gd_lower = (v[1] - v[4]) / 1000.0
            self.gd_buf, self.gd_topups, self.gd_stable = [], 0, True
            self.gd_baseline = self.gd_peak = self.gd_progress = self.gd_dispensed = self.gd_last_gain = 0.0
            self.gd_result = GR_NONE
            self.gd_started_at = self.gd_last_sample_at = self.gd_pump_started_at = now
            wanted = int(self.cfg['scale']['stream_hz'])
            if self.stream_hz < wanted:
                self.stream_hz = wanted
            self._fail_count = 0
            self.gd_phase = GD_BASELINE
            self._emit('OK,dose_started')
            self._emit(self._state_line())

        # ---- the dose, as in the firmware ---------------------------------------------------------
        def _dose_status(self):
            active = self.gd_phase != GD_IDLE
            gain = self.gd_dispensed if (not active and self.gd_result == GR_DONE) else self.gd_last_gain
            elapsed = (time.monotonic() if active else self.gd_ended_at) - self.gd_started_at
            return ('GSTAT,active=%d,phase=%s,result=%s,motor=%d,target=%.3f,gain=%.3f,baseline=%.3f,topups=%d,stable=%d,ms=%d'
                    % (1 if active else 0, PHASE_NAMES[self.gd_phase], RESULT_NAMES[self.gd_result], self.gd_motor,
                       self.gd_target, gain, self.gd_baseline, self.gd_topups, 1 if self.gd_stable else 0,
                       int(round(elapsed * 1000))))

        def _dose_print_phase(self):
            self._emit('DOSE,phase=%s,gain=%.3f' % (PHASE_NAMES[self.gd_phase], self.gd_last_gain))
            self._emit(self._state_line())

        def _dose_run(self, sps):
            self._queue_motor_start(self.gd_motor, True, 0, float(sps))

        def _dose_stop_pump(self):
            gid = self._group_of(self.gd_motor)
            if gid is not None:
                self._stop_group(gid)

        def _dose_finish(self, result):
            """Whatever the reason: the pump stops first, then the result. No STATE line."""
            if self.gd_phase == GD_IDLE:
                return
            self._dose_stop_pump()
            self.gd_result = result
            self.gd_ended_at = time.monotonic()
            self.gd_phase = GD_IDLE
            gain = self.gd_dispensed if result == GR_DONE else self.gd_last_gain
            self._emit('DOSE,end,result=%s,gain=%.3f,target=%.3f,topups=%d,ms=%d'
                       % (RESULT_NAMES[result], gain, self.gd_target, self.gd_topups,
                          int(round((self.gd_ended_at - self.gd_started_at) * 1000))))

        def _dose_end(self, result):
            if self.gd_phase == GD_IDLE:
                return
            self._dose_finish(result)
            self._emit(self._state_line())

        def _dose_settle(self, now):
            self._dose_stop_pump()
            self.gd_settle_until = now + self.gd_settle_s
            self.gd_phase = GD_SETTLE
            self.gd_buf = []
            self._dose_print_phase()

        def _dose_sample(self, grams, stable):
            if self.gd_phase == GD_IDLE:
                return
            now = time.monotonic()
            self.gd_last_sample_at = now
            if self.gd_phase == GD_BASELINE:
                self.gd_buf.append(grams)
                if len(self.gd_buf) >= self.gd_base_n:
                    self.gd_baseline = statistics.median(self.gd_buf)
                    self.gd_buf, self.gd_last_gain = [], 0.0
                    self._dose_run(self.gd_sps[0])
                    self.gd_pump_started_at = self.gd_last_progress_at = self.gd_last_sample_at = time.monotonic()
                    self.gd_phase = GD_PRIMING
                    self._dose_print_phase()
                return
            if self.gd_phase == GD_SETTLE:
                return
            if self.gd_phase == GD_FINAL:
                if not stable:
                    self.gd_stable = False
                self.gd_buf.append(grams)
                if len(self.gd_buf) >= self.gd_final_n:
                    self.gd_dispensed = statistics.median(self.gd_buf) - self.gd_baseline
                    self.gd_last_gain, self.gd_buf = self.gd_dispensed, []
                    if self.gd_dispensed >= self.gd_lower:
                        self._dose_end(GR_DONE)
                        return
                    if now - self.gd_pump_started_at >= self.gd_timeout_s:
                        self._dose_end(GR_TIMEOUT)
                        return
                    self.gd_topups = min(self.gd_topups + 1, 255)
                    self.gd_progress = self.gd_dispensed
                    self._dose_run(self.gd_sps[2])
                    self.gd_last_progress_at = self.gd_last_sample_at = time.monotonic()
                    self.gd_phase = GD_TOPUP
                    self._dose_print_phase()
                return
            gain = grams - self.gd_baseline
            if self.gd_phase == GD_TOPUP:
                self.gd_last_gain = gain
                gid = self._group_of(self.gd_motor)
                if self._pending_start.get(gid) is not None:
                    return                               # the valve opens first: the pump is not turning yet
                if gain >= self.gd_lower:
                    self._dose_settle(now)
                    return
                if gain >= self.gd_progress + self.gd_min_gain:
                    self.gd_progress, self.gd_last_progress_at = gain, now
                if now - self.gd_last_progress_at >= self.gd_no_gain_s:
                    self._dose_end(GR_NO_GAIN)
                return
            self.gd_buf = (self.gd_buf + [gain])[-3:]
            measured = statistics.median(self.gd_buf)
            self.gd_last_gain = measured
            self.gd_peak = max(self.gd_peak, measured)
            if self.gd_phase == GD_PRIMING and self.gd_peak >= self.gd_min_gain:
                self.gd_phase = GD_COARSE
                self._dose_print_phase()
            if measured >= self.gd_progress + self.gd_min_gain:
                self.gd_progress, self.gd_last_progress_at = measured, now
            if measured >= self.gd_lower:
                self._dose_settle(now)
                return
            if now - self.gd_last_progress_at >= self.gd_no_gain_s:
                self._dose_end(GR_NO_GAIN)
                return
            remaining = self.gd_target - measured
            if remaining <= self.gd_trim_zone and self.gd_phase != GD_TRIM:
                self._dose_run(self.gd_sps[2])
                self.gd_phase = GD_TRIM
                self._dose_print_phase()
            elif remaining <= self.gd_slow_zone and self.gd_phase == GD_COARSE:
                self._dose_run(self.gd_sps[1])
                self.gd_phase = GD_APPROACH
                self._dose_print_phase()

        def _service_dose(self):
            if self.gd_phase == GD_IDLE:
                return
            now = time.monotonic()
            if self.gd_phase in PUMPING and now - self.gd_pump_started_at >= self.gd_timeout_s:
                self._dose_end(GR_TIMEOUT)
                return
            if self.gd_phase == GD_SETTLE:
                if now >= self.gd_settle_until:
                    self.gd_phase, self.gd_buf, self.gd_stable = GD_FINAL, [], True
                    self.gd_last_sample_at = now
                return
            if now - self.gd_last_sample_at >= self.gd_sample_s:
                self._dose_end(GR_SCALE)

        def _service_watchdog(self):
            """The PC has been silent for too long: the runs that have no end of their own (V) stop. A finite
            dose (P), the cleaning and a weight dose end by themselves and are left alone."""
            if self.wd_ms == 0 or self.cleaning or self.gd_phase != GD_IDLE:
                return
            if (time.monotonic() - self.wd_last_rx) * 1000.0 < self.wd_ms:
                return
            stopped = False
            for gid in self.sel:
                pending = self._pending_start[gid]
                if self.mstate[gid] == 'run' or (pending is not None and pending['continuous']):
                    self._stop_group(gid)                # the linked valve closes after its delay
                    stopped = True
            if not stopped:
                return
            self._emit('WATCHDOG,host_silent,ms=%d' % self.wd_ms)
            self._emit(self._state_line())

    _classes[base] = PlasmicMega
    return PlasmicMega


def use(v7, link):
    """Make this one V7 Link build the simulator of this module when it starts (V7's `Link.start()` takes
    the class from its module at that moment: it is swapped for the length of that call only)."""
    transport = v7.transport
    original = link.start

    def start():
        with _swap:
            stock = transport.SimulatedMega
            transport.SimulatedMega = simulator_class(stock)
            try:
                return original()
            finally:
                transport.SimulatedMega = stock

    link.start = start
    return link
