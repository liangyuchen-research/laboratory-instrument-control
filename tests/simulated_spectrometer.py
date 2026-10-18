"""Stands in for "rpi_tools/hardware_verify.py spectrum ..." in the tests (see simulated_rig.py).

Same command line and the same limits on it, same files (progress.jsonl, frame-N.ready /
frame-N.go, spectrum-N.csv, spectrum.json), same waits (the go mark is looked for every 10 ms and
counts as taken when this program removes it; after 30 s without one it gives up), the same
checks in the same places (each frame's data after its call, the wavelength table only after the
last frame), the same report on standard output and the same exit codes (0, or 2 when the report
is not valid). Instead of the vendor SDK there is a pretend sensor that reports, for every frame,
when it began to integrate and how long the Nano's pulse output (the edges simulated_rig writes to
d13.log) was on during the exposure.

What is ASSUMED here, because it cannot be known without the spectrometer. A green test run says
nothing about these; the first real runs have to show them:

  * The sensor begins to integrate SENSOR_DELAY (2 ms; the wish "sensor_delay_ms" changes it)
    after the program has called the SDK's one-shot function. The real latency has never been
    measured. Everything the tests say about "the exposure is open when the first pulse comes"
    rests on this number; plasma_measure.py --lead-ms is there so it can be set on the rig.
  * The program calls the SDK PROGRAM_LATENCY (1 ms) after it has taken a frame's go mark: two
    lines go into progress.jsonl in between. The pretend sensor's opening is counted from the
    taking of the mark with that fixed number, not from when this computer really got through
    those two lines: Windows now and then needs several milliseconds for them, which says nothing
    about the RPi (whose SD card may be slow in its own way; the report's
    exposure_before_pulse_ms, taken from the real program's own log, will show it on the rig).
  * A one-shot call returns after the integration time plus 10 ms (21 ms for every third frame),
    the opening takes 80 ms and the settle time is the one asked for: as logged on the RPi on
    2026-10-05. Saving a frame costs what this computer needs; the RPi's SD card may be slower.
  * A call into the SDK cannot be cut short. A SIGTERM that comes during one is acted on when the
    call returns (Python runs a signal handler only between two of its own steps); while the
    program itself waits (for a go mark, the settle time) it acts at once. simulated_rig.Rig.killpg
    leaves the SIGTERM as a file "sigterm-<pid>" beside this program; SIGKILL is a real kill.
  * The clock is time.perf_counter (the real program uses time.monotonic; on the RPi they agree,
    on Windows monotonic moves in 16 ms steps), and on Windows only, removing a go mark that the
    tool is looking at this instant is tried again. Times of day ("opened_at") are that clock
    plus the rig's own offset (the wish "wall", which simulated_rig always sends): time.time() moves
    in 1 ms steps on Windows, and the same clock on both sides compares to the microsecond.

spectrometer.json beside this file says what should go wrong:

  {"speed": 4}             the fixed waits (start, opening, settle) are that much shorter
  {"open_failures": 1}     the next start fails like "UAI_SpectrometerOpen returned 6" did on site
  {"hang_open": 2}         the 2nd program started never comes back from UAI_SpectrometerOpen
  {"slow_open": 3.0}       UAI_SpectrometerOpen takes 3 s (whatever "speed" says)
  {"die_after": 2}         the program is gone (exit 3, no report) after two frames
  {"hang_frame": 1}        the one-shot call for frame 1 never returns
  {"slow_frame": [2, 1.5]} the one-shot call for frame 2 returns 1.5 s late
  {"slow_take": [3, 0.3]}  the program is stalled for 0.3 s before it looks for frame 3's go mark
  {"sensor_delay_ms": 12}  the pretend sensor begins to integrate that long after the call
  {"bad_frame": 2}         frame 2 comes back constant: refused right after its call
  {"bad_calibration": true}  the wavelength table is not increasing: found after the last frame
"""
import argparse
import csv
import json
import math
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIXELS = 1862
SENSOR_DELAY = .002
PROGRAM_LATENCY = .001
STOP = HERE / ('sigterm-%d' % os.getpid())


def progress(outdir, stage, **details):
    event = {'utc': datetime.now(timezone.utc).isoformat(), 'stage': stage, **details}
    with (outdir / 'progress.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event) + '\n')


def wishes():
    try:
        return json.loads((HERE / 'spectrometer.json').read_text(encoding='ascii'))
    except (OSError, ValueError):
        return {}


def asked_to_stop():
    """What the real program's SIGTERM handler does, at the places where Python could run it."""
    if STOP.exists():
        try:
            STOP.unlink()  # one signal, one exception: the closing call after it is not interrupted again
        except OSError:
            pass
        raise RuntimeError('Interrupted by signal 15')


def wait(seconds):
    """A wait of the program itself (time.sleep in the real one): a SIGTERM ends it at once."""
    end = time.perf_counter() + seconds
    while True:
        asked_to_stop()
        left = end - time.perf_counter()
        if left <= 0:
            return
        time.sleep(min(.005, left))


def native(seconds):
    """Time spent inside an SDK call: nothing cuts it short, the handler runs when it is over."""
    time.sleep(seconds)
    asked_to_stop()


def hang():
    """An SDK call that never returns: only a kill ends the program."""
    while True:
        time.sleep(3600)


def pulse_light(begin, end):
    """For how many ms the pulse output was on between the two times."""
    try:
        lines = (HERE / 'd13.log').read_text(encoding='ascii').split('\n')
    except OSError:
        return 0.0
    spans, rise = [], None
    for line in lines:
        kind, _, when = line.partition(' ')
        if kind == 'R':
            rise = float(when)
        elif kind == 'F' and rise is not None:
            spans.append((rise, float(when)))
            rise = None
    if rise is not None:
        spans.append((rise, end))
    return round(sum(max(0.0, min(end, stop) - max(begin, start)) for start, stop in spans) * 1000, 2)


def start_number():
    """The how-manieth spectrometer program of this rig this one is."""
    counter = HERE / 'starts.txt'
    try:
        number = int(counter.read_text(encoding='ascii')) + 1
    except (OSError, ValueError):
        number = 1
    counter.write_text(str(number), encoding='ascii')
    return number


def spectrum(args, report):
    outdir, plan = args.outdir, wishes()
    speed = float(plan.get('speed', 1))
    sensor_delay = float(plan.get('sensor_delay_ms', SENSOR_DELAY * 1000)) / 1000
    wall = float(plan['wall']) if 'wall' in plan else time.time() - time.perf_counter()  # time of day = perf_counter + wall
    started_as = start_number()
    frame_number = 0
    # the real program's limits, in its words (hardware_verify.spectrum_test)
    if not 1 <= args.integration_ms <= 10000:
        raise ValueError('Integration time must be 1–10000 ms')
    if not 0 <= args.sdk_settle_ms <= 2000:
        raise ValueError('SDK settle time must be 0–2000 ms')
    if not 1 <= args.frames <= 1000:
        raise ValueError('Frame count must be 1–1000')
    if not 0 <= args.frame_interval_ms <= 60000:
        raise ValueError('Frame interval must be 0–60000 ms')

    def call(name, seconds, code=0, stuck=False):
        progress(outdir, 'sdk_call_start', function=name, frame=frame_number)
        if stuck:
            hang()
        native(seconds)
        progress(outdir, 'sdk_call_done', function=name, frame=frame_number, code=code)
        report.setdefault('sdk_returns', []).append({'function': name, 'code': code})
        if code:
            raise RuntimeError('%s returned %s' % (name, code))

    progress(outdir, 'library_load_start')
    native(.005 / speed)
    progress(outdir, 'library_load_done')
    report['valid'] = False
    if plan.get('open_failures', 0) > 0:
        plan['open_failures'] -= 1
        (HERE / 'spectrometer.json').write_text(json.dumps(plan), encoding='ascii')
        call('UAI_SpectrometerOpen', .009, 6)
    call('UAI_SpectrometerOpen', float(plan.get('slow_open', .08 / speed)), stuck=plan.get('hang_open') == started_as)
    try:
        for name in ('UAI_SpectrometerGetSerialNumber', 'UAI_SpectrometerGetModelName',
                     'UAI_SpectromoduleGetFrameSize', 'UAI_SpectrometerWavelengthAcquire'):
            call(name, .001)
        wavelength = [180 + index * 320 / (PIXELS - 1) for index in range(PIXELS)]
        if plan.get('bad_calibration'):
            wavelength[10] = wavelength[9]
        report.update(serial='SIMULATED0001', model='simulated-OES', pixels=PIXELS,
                      wavelength_range_nm=[wavelength[0], wavelength[-1]], integration_ms=args.integration_ms,
                      sdk_settle_ms=args.sdk_settle_ms, frames_expected=args.frames,
                      frame_interval_ms=args.frame_interval_ms, triggered=args.triggered, frames=[])
        if args.sdk_settle_ms:
            progress(outdir, 'sdk_settle_start', milliseconds=args.sdk_settle_ms)
            wait(args.sdk_settle_ms / 1000 / speed)
            progress(outdir, 'sdk_settle_done', milliseconds=args.sdk_settle_ms)
        previous_start = None
        for index in range(args.frames):
            frame_number = index + 1
            taken = None
            if previous_start is not None and args.frame_interval_ms:
                wait(max(0, previous_start + args.frame_interval_ms / 1000 - time.perf_counter()))
            if args.triggered:
                ready = outdir / f'frame-{frame_number}.ready'
                go = outdir / f'frame-{frame_number}.go'
                if ready.exists() or go.exists():
                    raise RuntimeError('Stale frame trigger marker')
                ready.write_text(str(frame_number), encoding='ascii')
                progress(outdir, 'frame_ready', frame=frame_number)
                slow = plan.get('slow_take') or [0, 0]
                if slow[0] == frame_number:
                    time.sleep(slow[1])  # stalled (a busy SD card, say), not waiting: nothing is looked at
                trigger_deadline = time.perf_counter() + 30
                while not go.is_file():
                    if time.perf_counter() >= trigger_deadline:
                        raise TimeoutError(f'Frame {frame_number} trigger not received within 30 s')
                    wait(.01)
                for attempt in range(200):
                    try:
                        go.unlink()
                        break
                    except PermissionError:  # Windows only: another program is looking at the file this instant
                        if attempt == 199:
                            raise
                        time.sleep(.0005)
                taken = time.perf_counter()
                progress(outdir, 'frame_triggered', frame=frame_number)
            started = time.perf_counter()
            previous_start = started
            progress(outdir, 'sdk_call_start', function='UAI_SpectrometerDataOneshot', frame=frame_number)
            # When the pretend sensor begins to integrate, as a time of day (see the assumptions above).
            opened = (time.perf_counter() if taken is None else taken + PROGRAM_LATENCY) + sensor_delay + wall
            if plan.get('hang_frame') == frame_number:
                hang()
            late = plan.get('slow_frame') or [0, 0]
            native(args.integration_ms / 1000 + (.021 if index % 3 == 0 else .010)
                   + (late[1] if late[0] == frame_number else 0))
            progress(outdir, 'sdk_call_done', function='UAI_SpectrometerDataOneshot', frame=frame_number, code=0)
            report['sdk_returns'].append({'function': 'UAI_SpectrometerDataOneshot', 'code': 0})
            elapsed = time.perf_counter() - started
            if plan.get('bad_frame') == frame_number:
                raise RuntimeError('Nonfinite, missing or constant spectrum data')
            light = pulse_light(opened, opened + args.integration_ms / 1000)
            values = [700 + 30 * math.sin(x / 23) + light * 20 * math.exp(-((x - 337.13) / .6) ** 2) for x in wavelength]
            filename = 'spectrum-%d.csv' % (index + 1)
            with (outdir / filename).open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(['wavelength_nm', 'intensity_raw'])
                writer.writerows(zip(wavelength, values))
            report['frames'].append({'file': filename, 'elapsed_s': elapsed, 'minimum': min(values),
                                     'maximum': max(values), 'light_ms': light, 'opened_at': opened})
            if plan.get('die_after') == frame_number:
                os._exit(3)
        report['valid'] = True
    finally:
        call('UAI_SpectrometerClose', .02 / speed)
    return wavelength


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('spectrum',))
    parser.add_argument('--app-dir', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--integration-ms', type=int, default=100)
    parser.add_argument('--frames', type=int, default=3)
    parser.add_argument('--frame-interval-ms', type=int, default=0)
    parser.add_argument('--triggered', action='store_true')
    parser.add_argument('--sdk-settle-ms', type=int, default=500)
    args = parser.parse_args()
    time.sleep(.1 / float(wishes().get('speed', 1)))  # Python itself starts more slowly on the RPi
    args.outdir.mkdir(parents=True, exist_ok=True)
    report = {'action': args.action, 'started_utc': datetime.now(timezone.utc).isoformat(), 'valid': False}

    def interrupted(signum, _frame):
        raise RuntimeError('Interrupted by signal %d' % signum)
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    try:
        progress(args.outdir, 'process_start', pid=os.getpid(), integration_ms=args.integration_ms,
                 frames=args.frames, triggered=args.triggered)
        wavelength = spectrum(args, report)
        # as in the real program: the whole table is only looked at once the SDK is closed
        if not all(wavelength[i] < wavelength[i + 1] for i in range(len(wavelength) - 1)):
            raise RuntimeError('Invalid wavelength calibration')
    except Exception as exc:
        report.update(valid=False, error=str(exc))
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (args.outdir / 'spectrum.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
