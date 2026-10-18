# Release verification

This release uses the supplied Autonomous Laboratory v6 source. Checks were run on the English edition using isolated simulator configuration and calibration files.

## Completed checks

| Check | Result |
| --- | --- |
| End-to-end simulator self-test | 105 passed, 0 failed |
| Focused regression suite | 8 passed |
| Python syntax in v6 backend and tools | 16 files parsed successfully |
| Firmware configuration | Generated header matches the shared YAML constants |
| Arduino Mega 2560 compilation | Passed; 27,780 bytes flash and 1,969 bytes static RAM |
| Browser console | Simulator connection, live weight updates, motor selection, volume-dose command, and Stop All checked |

The Python checks used Python 3.12.14 and the pinned dependencies in `requirements.txt`. Firmware was compiled with Arduino CLI 1.5.1, Arduino AVR core 1.8.8, and AccelStepper 1.64.0. The compiler reported no sketch warnings; four unused-parameter warnings came from the Arduino core.

To rerun the software checks:

```bash
python tools/selftest.py
python -m unittest tools.test_regressions -v
```

The self-test launches an HTTP server on an available loopback port with simulation forced on. It verifies device operations, conversion factors, gravimetric dosing, valve interlocks, cleaning transitions, error responses, scale persistence, SSE events, documentation routes, and frontend operation names. It uses temporary test files outside the checkout.

## Software corrections

The English edition includes focused corrections to the supplied code:

- Numeric parameters and incoming scale values reject NaN and infinity before they affect commands or state.
- Malformed JSON returns HTTP 400 without invoking an operation. Previously it could be treated as an empty parameter object.
- Serial commands require the expected completion line. Incidental messages no longer make a partial response appear successful.
- Completion matching includes the firmware's `CFG,` reply to `W,SET` and trimmed `W,HELP` output.
- Scale diagnostics are blocked while peristaltic motion, gravimetric dosing, or cleaning is active.
- Self-tests isolate configuration and calibration output. Windows launchers preserve nonzero failure exit codes.

The firmware control flow, pins, numeric constants, motion timing, and pump coefficients were preserved. English diagnostic completion messages are `SCAN,DONE` and `SNIFF,TOTAL`; the backend and firmware in this checkout must be used together.

## Hardware validation scope

No physical serial port was opened, no firmware was uploaded, and no live pump or spectrometer experiment was run for these checks. Compilation and simulator results do not establish physical dispensing accuracy, electrical timing, or calibration validity on another assembly.

The source firmware's long scale diagnostics continue servicing stepper pulses but defer new USB commands and cleaning transitions until the diagnostic completes. The API guard checks known peristaltic activity. Syringe motion is not represented in the cached state, so run diagnostics only when the entire physical apparatus is idle. The timing implementation was retained rather than changed without hardware validation.

Use the [hardware setup and calibration notes](../README.md#hardware-configuration) before a physical deployment. The preserved earlier pulse-control and spectral-acquisition implementation has its own [verification scope and limitations](../legacy/instrument-control/README.md).

## Legacy implementation review

The full-tree review on 17 September 2026 also checked every legacy directory, including the vendor source trees and serialized Xcos model. The v6 code remained unchanged from the checks above.

- The STM32F103C8 firmware compiled and linked with Arm GNU GCC 7.2.1 using the new standalone `build.py` recipe: 18,992 bytes text, 504 bytes data and 1,912 bytes BSS.
- The separately attributed RS485 Arduino variant compiled for the Uno target with AVR core 1.8.8 and LiquidCrystal I2C 1.1.2: 12,940 bytes flash and 581 bytes static RAM. The sketch produced no warnings; dependency warnings were confined to the LCD library and Arduino core.
- Six acquisition regression checks cover current exported-spectrum filenames, background handling, wavelength alignment, UART failure propagation, cleanup, release of a waiting pulse thread, and timeout-budget validation before connection. Three desktop failure checks and the fixed-width protocol check also passed.
- The Xcos archive passed CRC checks. All four members were inspected, including the XML diagram and Java-serialized Scilab dictionary, with no personal workstation paths or Chinese text found. The model was not executed.

The legacy reader now matches the Raspberry Pi export format, and failed runs cannot reuse a prior run's file list. Acquisition-worker failures propagate to the process instead of leaving a pulse thread waiting indefinitely. Full timestamp precision prevents overwriting closely spaced spectra. Unimplemented designer controls are hidden; the window-closing button is labeled **Close UI**, since remote cancellation is not implemented.

The vendor spectrometer SDK remains an external dependency. The host watchdog calculation now converts its millisecond timing budget to whole seconds with upward rounding and rejects values outside the protocol field before connection. Firmware timing is unchanged. Physical pulse timing, watchdog operation and live acquisition still require validation on the original instrument, as detailed in the [legacy protocol notes](../legacy/instrument-control/docs/hardware-and-protocol.md).
