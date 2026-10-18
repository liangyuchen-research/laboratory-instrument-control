# Fix plan for the review of the unified measuring flow (lead's decisions, 2026-10-06)

Inputs
- All 53 findings with scenario, evidence, proposed fix and the verdicts that exist:
  `...\scratchpad\review-findings.txt` (300 KB; search by finding id, e.g. `=== [high] stop-swallowed-by-off-check-reader`).
- The reviewers' scratch folders: `...\scratchpad\review\<lens or verify-id>\` — experiments and ready-made tests.
  Most useful: `review\test-quality\tests\test_measure_gaps.py` (gap tests that pass or fail on the unmodified code),
  `review\test-quality\tests\simulated_spectrometer_gaps.py`, `review\test-quality\make_mutants.py` + `mutants\` + `logs\`,
  `review\exits\exp_exits.py`, `review\refute-stop-swallowed\tests\exp_swallow.py`, `review\tcpd-drives-tool\`.
- A copy of the files as they were before the fixes: `...\scratchpad\review\before\` (for diffs; do not edit).

Rules that do not change
- The 22 files of `SHA256SUMS.txt` are never edited (`rpi\rpi_tools\*`, `rpi\app\*`, `nano\*`, ...). Every fix lives in
  `rpi\tcpd\plasma_measure.py`, `rpi\tcpd\plasmic_tcpd.py`, the installer, or the tests/simulators.
- Standard library only; Python 3.7-compatible syntax on the RPi side (32-bit Raspberry Pi OS); files LF (the `.sh` too).
- Tests touch only 127.0.0.1 and the emulated rig; `time.perf_counter()` for anything measured in milliseconds on Windows.
- On-screen / error texts are Traditional Chinese, short, exact about what is known ("Confirmed to close" only when it was confirmed).
- `VERSION` strings stay as they are in this round (the lead raises both sides to 2.0.0 with the PC program).

## Decisions

D1. **A stop is never lost.** `plasma_job.Interrupts` records every signal in `INTERRUPTS.received` even when the
    exception is later swallowed by a retry loop. The tool must look at that list: after every use of the retried
    off-check reader (including the reads inside `prepare()`), before ARM, immediately before `power_write(4, 1)`, before
    START, and before every `fire()`. A recorded signal → raise the usual interrupt error → the normal OFF sequence.
    A stop before HSPY ON must end with the supply never switched on (`supply writes == [(4, 0)]` at most) and no ARM.
    (Cleanup itself still ignores further signals, as today.)

D2. **Every wait while the supply may be ON has its own short deadline** and expiry goes straight to the OFF sequence
    (outputs off, Nano STOP, confirmation), long before the Nano's own 30 s trigger timeout:
    - Background frame: keep the head start (`RAMP_HEADSTART_SECONDS`, the owner wants the first discharge within a few
      seconds), but the background frame must be reported finished by `due + 2 s`; otherwise abort. The supply is then on
      for at most about 3.5 s with nothing recorded, instead of 32 s.
    - Each signal frame: the program must be ready for the next frame by `last_go + integration + 3 s` (and never later
      than the pace requires + 3 s); otherwise abort.
    - A trigger the Nano did not see: after `fire()`, the next status that still shows WAIT_TRIGGER with
      `loops_completed < triggered` (allowing for the time a status read takes) → abort with
      `D12 The trigger has not been Nano received`. Status reads must not delay the next edge (D4).
    - The go mark must be taken within `GO_TAKEN_SECONDS = 0.5` (was 0.2; the real Pi may be slower). No heartbeat is
      sent while waiting for the pick-up (a heartbeat there could separate exposure and discharge by 0.26 s); 0.5 s keeps
      the 2 s lease safe. Record `taken_ms` as today; beyond 0.5 s abort as today.

D3. **Parameters that cannot work are refused before anything is switched**, by the server (`check_parameters`, so they
    cannot even be saved, and again when `plasma_run` starts, for parameters written by an older program) and by the
    tool itself (same rule, own copy). One definition, used for refusal, estimates and deadlines:

    ```
    train_s  = Cycle * (Ontime + Offtime) / 1000            # ms values as in parameter.json
    period_s = max(Spectra_Interval / 1000, train_s + 0.15, 0.25)   # what one loop really takes on the 9600-baud bus
    refuse when
      Loopnum >= 2 and period_s > 28            -> cannot exceed between two rounds 28 seconds (Nano The upper limit of waiting for triggering is 30 seconds)
      Loopnum * period_s > 100                  -> existing 100 s rule, now with the real period; say the real numbers
      Loopnum > 999                             -> spectrometer program at most once 1000 open (background 1 open＋Most signals 999 open) 
    ```
    Consequences: the ">25 s" hold-back (`PROGRAM_WAIT_SECONDS`, `program_interval_ms`) is deleted — the spectrometer
    program always runs with interval 0 and the orchestrator alone paces the frames; the first discharge is never held
    back by a long interval. The 0.15 s / 0.25 s figures come from the reviewers' measurements on the emulated bus
    (status read + heartbeat per loop); keep them as named constants with that comment. Requests with an interval
    shorter than `train_s + 0.15` are NOT refused: they run at `period_s` (as the verified tool paces at the train
    length) and the report says the real period. `estimate_seconds`, `estimate_start_seconds`, the tool's deadlines and
    `SimBackend` all use `period_s`. A test table must show the server's and the tool's rule agree case by case.

D4. **A heartbeat never delays a trigger edge** (spec item 6), including the cases found: train 0.6–0.75 s with an
    interval 0.25–0.45 s longer (the owner's defaults are a 0.75 s train; intervals of 1000, 1100, 1200 ms must hold
    ±30 ms), and intervals of 400–450 ms. Plan the last heartbeat of a running train with the next due moment in view;
    one bridging quiet stretch may be longer than `QUIET_SECONDS` as long as the lease age (including the 0.26 s
    exchange) stays under 1.6 s. Where the request is faster than the bus allows (`interval < train_s + 0.15`), pace at
    `period_s` evenly rather than alternating early/late.

D5. **The spectrometer program is always ended, and its end is reported separately from the high voltage.**
    Keep the current child in one holder so the restart's child is the one that gets ended. Ending = SIGTERM to its
    process group, wait 2 s, then SIGKILL, wait again. The report then says two separate things: whether the supply
    was confirmed off (`Confirmed to close` only with the read-back), and whether the spectrometer program ended. A program that
    had to be killed, or could not be ended, is reported as that (it holds the USB device) — never as "Work stop not confirmed" when
    the supply WAS confirmed off, and never hidden.

D6. **The report tells the truth.** `timeline_s['off_confirmed']` only when confirmed; a record that HSPY ON was
    *attempted* is written before the write (`hv_on_attempted`), so an interrupted or unechoed ON still shows; a
    `BaseException` (KeyboardInterrupt, SystemExit) leaves `valid: false` and an error text; the stage file says `off`
    only once the outputs are being switched off (write order as the finding proposes). `valid: true` requires that the
    run completed AND the off was confirmed AND every frame is present.

D7. **Server (`plasmic_tcpd.py`)**
    - A `stop` that arrives while `plasma_run` is still starting (bus taken, job object not yet published) cancels that
      start; a `stop` that finds the bus busy re-checks for a running job and signals it instead of answering `busy`.
      Refused stops are audited.
    - Defence in depth for D1: if a SIGTERM was sent and, after 3 s, the tool's stage file is not `off` and the process
      is still running, send SIGTERM once more (at most one repeat; the 60 s kill stays). Update the test that pins
      "exactly one SIGTERM" accordingly.
    - `plasma_run` carrying a `sequence` field (an old PC program) is refused with
      `The computer program is an old version (Still choosing“fast/original”). Please update the computer program and try again.` — never silently run as something else.
    - `flow: "unified"` with `plasma_measure.py` missing or unloadable → `plasma_run` is refused with a clear message
      and `hello`/`status` say so; the verified tool is used only when the config says `"flow": "verified"`.
    - `load_plasma_result` accepts the largest legal unified report (999 loops): raise the limit (2 MB) — and the tool
      keeps `triggers` entries compact (short keys are NOT needed; just no redundant fields).
    - Watchdog counted from when the job actually starts, not from when the command line was received.
    - `poll_s` and the 20 s freshness rule cannot collide: cap the skip-the-pre-read age at 10 s whatever `poll_s` is.
    - New optional config key `exposure_lead_ms` (default 5, allowed 0–200, validated in `load_config`, documented in
      `config.example.json`) passed to the tool as `--lead-ms`; the tool uses it instead of the constant. Reason: the
      real sensor's start latency is unknown until the rig is tried; this lets it be tuned on site without new code.
    - `SimBackend` mirrors D3 (same refusals, same period).
    - Installer, tcpd-only path: if the RPi's `rpi_tools\plasma_job.py` differs from the package's protected copy, do
      not install the unified tool on top of it silently — stop with a message that says a full install is needed
      (and why: the new flow calls functions of that file). Keep the existing behaviour for a matching file.

D8. **Simulators must not be more forgiving than the real thing** where that hides a defect. Fix what the two divergence
    lists (`simulated-rig-more-forgiving-than-hardware`, `simulated-spectrometer-divergences`) and `simulated-rig-pin-race-phantom-train`
    show, by reading the firmware (`nano\source_split\ControlCore.h` and the sketch around it) and
    `rpi\rpi_tools\hardware_verify.py` (go-mark polling period, when a frame counts as taken, the 30 s give-up, the
    1000-frame limit, the calibration check after the last frame, exit codes). Things that cannot be known without the
    rig (the sensor's exposure-start latency) are stated as assumptions in one comment block at the top of the simulated.

D9. **Tests.** Bring the reviewers' passing gap tests into `tests\test_measure.py` (h1, c1, a6b, g1/g2, b4, d4, the
    stage sweeps a2–a5/a7, c2–c6, d1, d3, e1, e2, e6, f1–f6, h2 with `first_pulse < 5`, i1/i2, j1 — see the finding
    `existing-suite-lets-broken-safety-behaviour-pass`), turn the failing ones into passing tests of the fixed
    behaviour (stop during a pre-ON read, hung background, trigger not seen, over-limit interval refused, long train
    refused, many short loops, 999/1000 loops, restarted child ended, hung child killed), fix the flaky interval test,
    and strengthen `assert_everything_off` (final power reads OFF/0 V, no spectrometer program left). The nine mutants
    of `make_mutants.py` (re-targeted to the new code) must all be killed. Keep the whole file under about 8 minutes;
    use the simulated's `speed` and short parameters where the behaviour does not depend on real time.

## Who fixes what (disjoint files)

| Fixer | Owns | Decisions |
|---|---|---|
| A — tool | `rpi\tcpd\plasma_measure.py`, `tests\test_measure.py`, `tests\simulated_rig.py`, `tests\simulated_spectrometer.py` | D1, D2, D3 (tool side), D4, D5, D6, D8, D9, `--lead-ms` |
| B — server | `rpi\tcpd\plasmic_tcpd.py`, `rpi\tcpd\config.example.json`, `tests\test_tcpd.py`, `installer\install.py`, `rpi\install_rpi.sh`, `tests\test_installer.py` | D3 (server side, estimates, SimBackend, agreement table), D7 |

Not in this round (the lead does them with the PC program): `pc\*`, `tests\test_app.py`, the guide, `historical-development-notes.md`,
`docs\work record.md`, version numbers. `tests\test_app.py` is known to fail against the new server until then.
