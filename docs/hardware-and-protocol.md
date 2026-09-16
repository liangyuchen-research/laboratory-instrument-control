# Hardware and protocol notes

## STM32 controller defaults

The supplied firmware targets STM32F103C8T6 and uses TIM2 for pulse timing. Its initialization sets a prescaler of 719 for a nominal 72 MHz timer clock, giving 0.01 milliseconds per count. The source identifies PA1 as the pulse output and PB10 as the rising-edge trigger input. UART2 receives condition commands at 9,600 baud, eight data bits, no parity, and two stop bits.

These are source-code defaults, not a verified wiring specification. Confirm the actual board, clock tree, pin mapping, voltage interface, and firmware variant before operation.

## Raspberry Pi configuration

| Environment variable | Original/default value | Purpose |
| --- | --- | --- |
| `LAB_SERIAL_PORT` | `/dev/serial0` | UART device |
| `LAB_SERIAL_BAUD` | `9600` | Serial baud rate |
| `LAB_TRIGGER_PIN` | `16` | Physical BOARD-numbered GPIO pin, corresponding to GPIO23 in the source comments |
| `LAB_SPECTROMETER_LIBRARY` | Required | Platform-specific vendor shared-library path |
| `LAB_DATA_DIR` | `raspberry_pi/acquisitions/` beside the controller | Root directory for generated spectra |

The source selects BOARD pin numbering. Changing a pin number does not change that numbering convention.

For the desktop, `LAB_CONFIG_FILE` can select another INI file. `LAB_PI_USER`, `LAB_PI_HOST`, `LAB_PI_CONTROLLER`, and `LAB_PI_DATA_DIR` override its connection fields. The remote data directory must match the Raspberry Pi acquisition output. `LAB_PI_PASSWORD` supplies an optional password, while the preserved Paramiko connection can also use its default key/agent behavior when no password is set.

## UART condition message

The controller writes an ASCII line beginning with `condition`, followed by fixed-width fields:

| Payload digits | Meaning | Firmware conversion |
| --- | --- | --- |
| 0-2 | On-time integer milliseconds | Integer value |
| 3-5 | On-time fractional field | Divide by 100 |
| 6-9 | Off-time milliseconds | Integer value |
| 10-13 | Pulse cycle count | Integer value |
| 14-17 | Voltage parameter | Integer value |
| 18-21 | Timeout | Interpreted as seconds |

A newline terminates the command. The `stop` command requests pulse shutdown. The contributed Arduino/RS485 sketch has a different `ADDR:` envelope and payload layout, documented in its source.

## Acquisition behavior

The Raspberry Pi opens the spectrometer through `ctypes`, reads the wavelength grid, and acquires a background spectrum before measurement. Mode 1 writes the measured spectrum, and mode 2 writes the spectrum minus the stored background. Acquisition and pulse threads coordinate using an in-memory signal list and software delays; the source does not establish deterministic hardware-trigger synchronization or measured timing accuracy.

Generated text files contain wavelength and intensity columns. Filenames encode timestamps and measurement parameters. The desktop fetches the most recent date-organized acquisition directory and plots spectra or selected-wavelength traces.

## Preserved limitations to review before operation

- The source computes `est_to_ms` and then rounds it directly into `est_to_sec`, without dividing by 1,000. The firmware interprets the transmitted field as seconds. This unit inconsistency is documented rather than silently changing experimental timing during packaging.
- The fractional on-time carry guard checks `1000` even though the fractional field is calculated by multiplying by `100`. Input range handling and rounding should be verified for the intended instrument settings.
- The serial format has fixed-width minimum formatting but does not reject overflow values. The offline test checks representative valid parameters only.
- Closing the desktop GUI does not stop an active remote acquisition. The source's local stop-file field is not implemented as a complete remote cancellation mechanism.
- The GUI retains automatic acceptance of unknown SSH host keys from the legacy source. Review connection policy for the intended instrument network before deployment.
- The `.ioc` file does not fully describe all manually added GPIO/interrupt changes in `Core/Src/main.c`. Inspect both before regeneration or flashing.
- The spectrometer SDK is not included, and no compatibility claim is made for another OS, CPU architecture, spectrometer, or library version.

Offline checks cover source syntax, command encoding, and remote failure handling. A nonzero remote exit code prevents the desktop from treating the run as successful and fetching previous acquisition files. The checks use in-memory serial, GPIO, and SSH substitutes. Firmware compilation, live acquisition, and model simulation remain unverified.
