"""pump control board (pc/pumpboard.py) Test common tools.

  * Set up as soon as you import it PLASMIC_NO_SERIAL=1 (before loading anything else);
  * install_tripwire(): Bundle V7 in the transport layer serial is replaced with something that ”records it and reports an error as soon as it is called”, and the port list is replaced with a simulated one;
  * make_board() / Rig: is in the temporary folder, use V7 Create a control board for your own simulator and shorten the time;
  * FaultPlan: creates a fault on the simulator (reply missing, error response, wire unplugged, board restart...);
  * Script: Give link_factory is used to test the port selection and identity check of ”real mode”.

Nothing here will open a real port or ask what ports this computer has.
"""
from __future__ import annotations

import os

os.environ['PLASMIC_NO_SERIAL'] = '1'      # the kill switch, before anything else is imported

import contextlib  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import queue  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
from pathlib import Path  # noqa: E402

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PC = ROOT / 'pc'
VENDOR = PC / 'vendor'
if str(PC) not in sys.path:
    sys.path.insert(0, str(PC))

import pumpboard  # noqa: E402

READY = 'READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0'
opened = []          # every attempt to open a serial port in this process; must stay empty


def simulated_port(device, description, vid=None, pid=None, serial_number=None):
    """What pyserial's port list returns for one port (only the fields the program reads)."""
    return types.SimpleNamespace(device=device, description=description, vid=vid, pid=pid,
                                 serial_number=serial_number, manufacturer=None)


MEGA = simulated_port('COM91', 'Arduino Mega 2560 (COM91)', 0x2341, 0x0042, 'TEST0000000000000091')
CLONE = simulated_port('COM94', 'Arduino Mega 2560 (COM94)', 0x2A03, 0x0042, 'TEST0000000000000094')
CH340 = simulated_port('COM92', 'USB-SERIAL CH340 (COM92)', 0x1A86, 0x7523, None)
PLAIN = simulated_port('COM93', 'communication port Is the (COM93)', None, None, None)
SIMULATED_PORTS = [MEGA, CH340, PLAIN]


def identity(port, **more):
    """pumpboard.json for a board that was chosen on this simulated port."""
    row = pumpboard.port_row(port)
    settings = {'version': 1, 'enabled': True, 'device': row['device'], 'vid': row['vid'], 'pid': row['pid'],
                'serial_number': row['serial_number'], 'description': row['description'],
                'chosen_at': '2026-10-06T10:00:00+08:00', 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4}
    settings.update(more)
    return settings


class Tripwire:
    """Stands where V7's transport expects the pyserial module."""

    def Serial(self, *args, **kwargs):
        opened.append((args, kwargs))
        raise AssertionError('a test tried to open a real serial port')


def install_tripwire():
    """After this, nothing in this process can open a serial port or read the real port list.

    V7 wraps what Serial() raises into its own error, so a test may not notice the AssertionError:
    every test module therefore calls assert_nothing_opened() in tearDownModule.
    """
    v7 = pumpboard.load_v7()
    wire = Tripwire()
    v7.transport.serial = wire
    v7.transport.list_ports = None
    pumpboard._comports = lambda: list(SIMULATED_PORTS)
    try:                                    # the vendored pyserial itself, should anything reach it directly
        import serial
        from serial.tools import list_ports
        serial.Serial = wire.Serial
        list_ports.comports = lambda *args, **kwargs: list(SIMULATED_PORTS)
    except Exception:
        pass
    return wire


def assert_nothing_opened():
    if opened:
        raise AssertionError('a test tried to open a real serial port: %r' % (opened,))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# Short times for the simulator. It does not scale cleaning times, valve delays or the settle time.
FAST = {'link.simulation_speedup': 20, 'valves.open_before_ms': 100, 'valves.close_after_ms': 100,
        'cleaning.water_lead_s': 0.3, 'cleaning.overlap_s': 0.3, 'cleaning.out_tail_s': 0.3,
        'motors.gravimetric_dose.settle_s': 0.2}


def setup_of(water=2, out=8, names=None, valves=None):
    """AMENDMENTS M20. A pump setup as the page sends it: who carries water, who drains, the named chemicals, the pump of each valve."""
    names = {9: 'CuSO₄'} if names is None else names
    pumps = []
    for motor in range(2, 10):
        if motor == water:
            pumps.append({'motor': motor, 'use': 'water'})
        elif motor == out:
            pumps.append({'motor': motor, 'use': 'out'})
        elif motor in names:
            pumps.append({'motor': motor, 'use': 'chemical', 'name': names[motor]})
        else:
            pumps.append({'motor': motor, 'use': 'none'})
    valves = valves or {}
    return {'pumps': pumps, 'valves': [{'id': valve, 'motor': valves.get(valve)} for valve in (1, 2)]}


def overrides_from(values):
    """The `overrides` seam of PumpBoard from a dict of dotted keys."""
    def apply(cfg):
        for dotted, value in values.items():
            node = cfg
            names = dotted.split('.')
            for name in names[:-1]:
                node = node[name]
            node[names[-1]] = value
    return apply


@contextlib.contextmanager
def patched(**values):
    """Shorten limits of pumpboard for one test: patched(DOSE_LIMIT_EXTRA_S=0.5, timeouts={'motor': 0.4})."""
    timeouts = values.pop('timeouts', None)
    before = {name: getattr(pumpboard, name) for name in values}
    before_timeouts = dict(pumpboard.TIMEOUT_S)
    try:
        for name, value in values.items():
            setattr(pumpboard, name, value)
        if timeouts:
            pumpboard.TIMEOUT_S.update(timeouts)
        yield
    finally:
        for name, value in before.items():
            setattr(pumpboard, name, value)
        pumpboard.TIMEOUT_S.clear()
        pumpboard.TIMEOUT_S.update(before_timeouts)


def wait_for(predicate, timeout=5.0, what='the expected state'):
    """`what` may be a function: it is asked only when the wait has failed, so it can say what is the case then."""
    limit = time.perf_counter() + timeout
    while time.perf_counter() < limit:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('timed out after %.1f s waiting for %s' % (timeout, what() if callable(what) else what))


class StubStore:
    """What PumpBoard needs from store.Store: the pump settings (pumpboard.json) and the event log."""

    def __init__(self, folder, settings=None):
        self.dir = Path(folder)
        self.path = self.dir / 'pumpboard.json'
        self.events = []
        self.saved = 0
        if settings is not None:
            self.save_pump_settings(settings)
            self.saved = 0

    def pump_settings(self):
        if self.path.is_file():
            return json.loads(self.path.read_text(encoding='utf-8'))
        return {'version': 1, 'enabled': None, 'syringe_ul': 5000, 'syringe_speed_ml_s': 0.4}

    def save_pump_settings(self, settings):
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        self.saved += 1

    def event(self, text, level='info', actor=None):
        self.events.append({'text': text, 'level': level, 'actor': actor})


class GuardRecorder:
    """Takes the place of the guard process: remembers what the board would have told it."""

    def __init__(self):
        self.started = []
        self.lines = []
        self.closed = []

    def start(self, vid, pid, serial_number, log_path):
        self.started.append((vid, pid, serial_number))

    def tell(self, line):
        self.lines.append(line)

    def close(self, bye=True):
        self.closed.append(bye)


class Rig:
    """One PumpBoard in a temp folder with everything a test wants to look at."""

    def __init__(self, folder=None, mode='sim', settings=None, calibration=True, start=True, gate=True,
                 link_factory=None, port_lister=None, store_events=False, sim_firmware='stock', **dotted):
        # sim_firmware: 'stock' = V7's own simulator (what most tests here are written for);
        # 'plasmic' = pc/megasim.py, the firmware of mega/ that the rig runs (weight dose on the board).
        self.own_folder = folder is None
        self.folder = Path(folder) if folder is not None else Path(tempfile.mkdtemp(prefix='plasmic-pump-'))
        self.store = StubStore(self.folder, settings)
        self.alarms = []             # every call of on_alarm: (action, key, priority, area, text, kind)
        self.active = {}             # the alarms that are raised now: key -> (priority, text)
        self.events = []             # every call of on_event: (text, level)
        self.page_age = 0.0          # what ui_age() answers
        self.hv = False              # what the high-voltage gate answers
        if calibration:
            target = self.folder / 'pumpboard' / 'scale_calibration.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(VENDOR / 'v7' / 'config' / 'scale_calibration.json', target)
        values = dict(FAST)
        values.update({name.replace('__', '.'): value for name, value in dotted.items()})
        self.board = pumpboard.PumpBoard(
            self.folder, mode, self.store, None if store_events else self._event, self._alarm, lambda: self.page_age,
            overrides=overrides_from(values), link_factory=link_factory, port_lister=port_lister,
            sim_firmware=sim_firmware)
        self.guard = GuardRecorder()
        self.board._guard = self.guard
        if gate:
            self.board.set_gate(lambda: self.hv)
        if start:
            try:
                self.board.start()
                if mode == 'sim':
                    self.wait_connected()
            except BaseException:
                self.close()             # never leave a live board behind: the next test could not start one
                raise

    def _event(self, text, level='info'):
        self.events.append((text, level))

    def _alarm(self, action, key, priority=None, area=None, text=None, kind=None):
        self.alarms.append((action, key, priority, area, text, kind))
        if action == 'raise':
            self.active[key] = (priority, text)
        else:
            self.active.pop(key, None)

    # -- looking at the board ---------------------------------------------------------------------
    @property
    def raw(self):
        """V7's Link of the present connection."""
        return self.board._conn.raw

    @property
    def sim(self):
        """V7's simulator behind the present connection."""
        return self.board._conn.raw._sim

    def wait(self, predicate, timeout=5.0, what='the expected state'):
        wait_for(predicate, timeout, what)

    def wait_connected(self, timeout=6.0):
        """Connected, and the first reading of the scale is in (the stream is switched on last)."""
        wait_for(lambda: self.board.connected, timeout,
                 lambda: 'the board to connect (it is %s: %s)' % (self.board.view()['link'], self.board.view()['text']))
        wait_for(lambda: self.board._weight[0] is not None, 3.0, 'the first reading of the scale')

    def wait_link(self, state, timeout=6.0):
        wait_for(lambda: self.board.view()['link'] == state, timeout,
                 lambda: 'link state %s (it is %s: %s)' % (state, self.board.view()['link'], self.board.view()['text']))

    def log_lines(self):
        """The serial log on disk: [(kind, text)] with kind tx / rx / -- (a note of the wrapper)."""
        self.board._flush_serial_log()
        path = self.folder / 'logs' / 'pumpboard.log'
        rows = []
        if path.is_file():
            for line in path.read_text(encoding='utf-8').splitlines():
                parts = line.split(' ', 2)
                if len(parts) == 3:
                    rows.append((parts[1], parts[2]))
        return rows

    def tx(self):
        """Every command written to the board so far, in order."""
        return [text for kind, text in self.log_lines() if kind == 'tx']

    def all_idle(self, sim=None):
        """Does the simulator itself say that nothing moves?"""
        sim = sim or self.sim
        return (not sim.cleaning and all(state == 'idle' for state in sim.mstate.values())
                and not any(sim.valves.values()))

    def spy(self):
        """Record what is handed to V7's send: [(command, terminators, timeout)]."""
        raw, calls = self.raw, []
        real = raw.send

        def send(command, terminators=None, timeout=None):
            calls.append((command, terminators, timeout))
            return real(command, terminators, timeout)

        raw.send = send
        return calls

    def motor(self, number):
        return next(row for row in self.board.view()['motors'] if row['id'] == number)

    def close(self):
        try:
            self.board.close()
        finally:
            if self.own_folder:
                shutil.rmtree(self.folder, ignore_errors=True)


def make_board(tmp=None, **overrides):
    """A started, connected board on V7's simulator (short times). Returns the Rig; the board is rig.board."""
    return Rig(tmp, **overrides)


class FaultPlan:
    """Faults for V7's simulator. While installed it wraps methods of backend.simulator.SimulatedMega
    (at run time; the vendored file is not touched). Use it as a context manager."""

    NAMES = ('_handle', 'readline', 'write', 'start', '_start_now')

    def __init__(self):
        pumpboard.load_v7()
        from backend import simulator
        import megasim
        self.cls = simulator.SimulatedMega
        # The model of the firmware of mega/ answers its own commands (G, K) before V7's _handle is asked:
        # its _handle is wrapped as well. The other four it inherits.
        self.sub = megasim.simulator_class(self.cls)
        self.sub_handle = self.sub.__dict__['_handle']
        self.rules = []
        self.cut = []                    # the simulators themselves (an id could be given to a later one)
        self.fail_starts = 0
        self.freeze_moves = False        # a finite move never ends
        self.instant_moves = False       # a finite move ends at once
        self.lock = threading.Lock()
        self.originals = {name: getattr(self.cls, name) for name in self.NAMES}
        plan, originals = self, self.originals
        inside = threading.local()

        def faulty(original):
            def handle(sim, line):
                if getattr(inside, 'busy', False):
                    return original(sim, line)       # the outer of the two wrappers has decided already
                inside.busy = True
                try:
                    rule = plan._match(line)
                    if rule is None:
                        return original(sim, line)
                    if rule['action'] == 'swallow':
                        return None
                    if rule['action'] == 'answer':
                        for text in rule['lines']:
                            sim._emit(text)
                        return None
                    real_emit, me = sim._emit, threading.get_ident()

                    def emit(text):
                        if threading.get_ident() != me:
                            real_emit(text)          # only what this command itself answers is lost

                    sim._emit = emit
                    try:
                        original(sim, line)
                    finally:
                        del sim._emit
                    return None
                finally:
                    inside.busy = False
            return handle

        handle = faulty(originals['_handle'])

        def readline(sim, timeout=0.2):
            if any(sim is pulled for pulled in plan.cut):
                raise OSError('cable pulled')
            return originals['readline'](sim, timeout)

        def write(sim, payload):
            if any(sim is pulled for pulled in plan.cut):
                raise OSError('cable pulled')
            return originals['write'](sim, payload)

        def start(sim):
            with plan.lock:
                failing = plan.fail_starts > 0
                if failing:
                    plan.fail_starts -= 1
            if failing:
                raise OSError('the simulated board does not start')
            return originals['start'](sim)

        def start_now(sim, gid, continuous, steps, sps, now=None):
            originals['_start_now'](sim, gid, continuous, steps, sps, now)
            if not continuous:
                if plan.freeze_moves:
                    sim._move_end[gid] = time.monotonic() + 3600.0
                elif plan.instant_moves:
                    sim._move_end[gid] = time.monotonic()

        for name, wrapper in (('_handle', handle), ('readline', readline), ('write', write), ('start', start),
                              ('_start_now', start_now)):
            setattr(self.cls, name, wrapper)
        self.sub._handle = faulty(self.sub_handle)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.restore()

    def restore(self):
        for name, original in self.originals.items():
            setattr(self.cls, name, original)
        self.sub._handle = self.sub_handle

    def _match(self, line):
        with self.lock:
            for rule in self.rules:
                if rule['times'] > 0 and line.upper().startswith(rule['prefix'].upper()):
                    if rule['skip'] > 0:
                        rule['skip'] -= 1
                        continue
                    rule['times'] -= 1
                    rule['hits'] += 1
                    return rule
        return None

    def _rule(self, prefix, action, times, skip, **more):
        rule = dict(prefix=prefix, action=action, times=times, skip=skip, hits=0, **more)
        with self.lock:
            self.rules.append(rule)
        return rule

    def drop(self, prefix, times=1, skip=0):
        """The command is carried out, its reply is lost. `skip`: let that many matching commands pass first."""
        return self._rule(prefix, 'drop', times, skip)

    def swallow(self, prefix, times=1, skip=0):
        """The command never reaches the board."""
        return self._rule(prefix, 'swallow', times, skip)

    def answer(self, prefix, lines, times=1, skip=0):
        """The board answers with these lines instead (and does not carry the command out)."""
        return self._rule(prefix, 'answer', times, skip, lines=list(lines))

    def clear(self):
        with self.lock:
            self.rules.clear()

    def pull_cable(self, sim):
        """From now on reading from and writing to this simulator raise, as a pulled USB cable does.
        The simulator itself goes on doing what it was doing, as the real board would — until the program
        gives the link up: V7's Link.stop() also stops its simulator, which a real board would not notice."""
        self.cut.append(sim)

    @staticmethod
    def no_weight_gain(sim):
        """Liquid no longer arrives on the scale."""
        sim._simulation_speedup = 0.0

    @staticmethod
    def reset_board(sim):
        """The board restarts: everything as after power-on, then the READY line."""
        with sim._lock:
            for gid in sim.sel:
                sim._pending_start[gid] = None
                sim._move_end[gid] = None
                sim.sel[gid] = None
                sim.mstate[gid] = 'idle'
                sim.sps[gid] = 0.0
            for valve in sim.valves:
                sim.valves[valve] = False
                sim._valve_close_at[valve] = None
            sim.cleaning = False
            sim._clean_out_started = sim._clean_water_stopped = False
            sim.pump_online = False
            sim.syringe_ul = int(sim.cfg['syringe_pump']['default_syringe_ul'])
            sim.stream_hz = int(sim.cfg['scale']['stream_hz'])
            sim.scale_div, sim.scale_offset = 1.0, 0.0
            if hasattr(sim, 'power_on'):
                sim.power_on()               # the firmware of mega/: no watchdog, no dose, no calibration
        sim._emit(READY)

    @staticmethod
    def flood(sim, count=500):
        """Many WEIGHT lines at once."""
        for index in range(count):
            sim._emit('WEIGHT,%.3f,RAW,%.1f,ST,1' % (index / 1000.0, float(index)))


class ScriptedDevice:
    """A simulated serial device for Script: answers each written line with what `respond(line)` returns."""

    def __init__(self, script, respond, banner=None):
        self.script, self.respond, self.banner = script, respond, banner
        self.out = queue.Queue()

    def start(self):
        if self.banner:
            self.out.put(self.banner)

    def stop(self):
        pass

    def readline(self, timeout=0.2):
        try:
            return self.out.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, payload):
        for line in payload.decode('utf-8', errors='replace').splitlines():
            line = line.strip()
            if line:
                self.script.written.append(line)
                for reply in self.respond(line):
                    self.out.put(reply)


class RecordedMega:
    """V7's simulator as the device behind a simulated port; remembers what was written to it."""

    def __init__(self, script, cfg, banner=True):
        from backend import simulator
        self.script, self.inner, self.banner = script, simulator.SimulatedMega(cfg), banner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def start(self):
        self.inner.start()                   # puts READY first, then starts ticking
        if not self.banner:
            self.inner._out.get_nowait()     # a board that did not restart when the port was opened
        elif self.banner is not True:
            self.inner._out.get_nowait()
            self.inner._out.put(self.banner)

    def stop(self):
        self.inner.stop()

    def readline(self, timeout=0.2):
        return self.inner.readline(timeout)

    def write(self, payload):
        for line in payload.decode('utf-8', errors='replace').splitlines():
            if line.strip():
                self.script.written.append(line.strip())
        self.inner.write(payload)


class Script:
    """A simulated serial port for PumpBoard(link_factory=...): the link is V7's own Link (its reader thread and
    its send() are the real ones) with a scripted device where pyserial would be.

    kind: 'mega' (V7's simulator) · 'mega-no-banner' (did not restart on open) · 'other-banner' (answers
    like the board but its READY line is another program's) · 'silent' · 'echo' · 'short-state'.
    """

    def __init__(self, kind='mega', open_error=None):
        self.kind, self.open_error = kind, open_error
        self.calls = []              # (device, the cfg['link'] dict) of every link that was built
        self.opened = []             # devices whose start() was called
        self.written = []            # every line written to the device
        self.closed = 0
        self.links = []

    def __call__(self, device, cfg):
        self.calls.append((device, cfg['link']))
        link = _simulated_link_class()(self, device)
        self.links.append(link)
        return link

    def device(self, cfg):
        if self.kind == 'mega':
            return RecordedMega(self, cfg)
        if self.kind == 'mega-no-banner':
            return RecordedMega(self, cfg, banner=False)
        if self.kind == 'other-banner':
            return RecordedMega(self, cfg, banner='READY,some-other-sketch,v1')
        if self.kind == 'silent':
            return ScriptedDevice(self, lambda line: [])
        if self.kind == 'echo':
            return ScriptedDevice(self, lambda line: [line])
        if self.kind == 'short-state':
            return ScriptedDevice(self, lambda line: ['STATE,a=-1,sa=idle'] if line == '?' else ['OK'])
        raise ValueError(self.kind)


SimulatedLink = Script        # the name the specification uses: PumpBoard(..., link_factory=SimulatedLink('mega'))
_SIMULATED_LINK = None


def _simulated_link_class():
    global _SIMULATED_LINK
    if _SIMULATED_LINK is None:
        v7 = pumpboard.load_v7()

        class SimulatedLink(v7.transport.Link):
            def __init__(self, script, device):
                super().__init__()           # takes the configuration PumpBoard has just made for this link
                self.script, self.device = script, device

            def start(self):                 # V7's start() with the scripted device instead of serial.Serial
                if self.connected:
                    return
                self.script.opened.append(self.device)
                if self.script.open_error:
                    self.connected = False
                    self.last_error = self.script.open_error
                    self._push_state()
                    raise v7.transport.LinkError(self.script.open_error)
                self._sim = self.script.device(self.cfg)
                self._sim.start()
                self.port_name = self.device
                self.connected = True
                self.last_error = None
                self._stop.clear()
                self._reader = threading.Thread(target=self._read_loop, daemon=True, name='serial-reader')
                self._reader.start()
                self._push_state()
                try:
                    self.send('?')
                except Exception:
                    pass

            def stop(self):
                self.script.closed += 1
                super().stop()

        _SIMULATED_LINK = SimulatedLink
    return _SIMULATED_LINK
