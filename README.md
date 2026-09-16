# Laboratory Instrument Control and Spectral Acquisition

Desktop-to-instrument control software for pulsed plasma experiments and optical spectroscopy. A Tkinter interface sends acquisition parameters to a Raspberry Pi, which coordinates an STM32 pulse controller and a spectrometer, then returns spectra for plotting and time-series analysis.

**Technologies:** Python, Tkinter, Paramiko/SCP, Raspberry Pi GPIO, UART, STM32 HAL, Scilab/Xcos.

## System architecture

```mermaid
flowchart LR
    A[Desktop acquisition interface] -->|SSH commands| B[Raspberry Pi controller]
    B -->|UART parameters and GPIO trigger| C[STM32 pulse controller]
    B -->|Vendor SDK| D[Optical spectrometer]
    B -->|Timestamped spectral files| E[Acquisition directory]
    E -->|SCP download| A
```

The controller supports background acquisition, raw or background-subtracted spectra, and wavelength-based time-series plots. Pulse and acquisition threads use software coordination. Measured synchronization accuracy is not available.

| Directory | Contents |
| --- | --- |
| `desktop/` | Parameter entry, remote control, file transfer, and spectrum visualization |
| `raspberry_pi/` | UART command encoding, pulse/acquisition coordination, and spectral export |
| `firmware/stm32/` | STM32F103C8T6 pulse generation, serial parsing, and trigger handling |
| `firmware/contributed_rs485_pulser/` | Separately attributed Arduino/RS485 controller variant |
| `models/` | Scilab/Xcos thermal feedback model |
| `scripts/` | Offline source, protocol, and failure-handling checks |

**Related research:** Autonomous Laboratory for Automated Solution Preparation and Quantitative Analysis, Plasma Engineering Laboratory, National Taiwan University. This repository contains the earlier pulse/spectrometer instrument. The later FastAPI application and multi-pump liquid-handling system are not included.

## Offline checks

Run from the repository root:

```bash
python scripts/validate_repository.py
python scripts/check_protocol.py
python scripts/check_desktop.py
```

These checks validate Python syntax, firmware command fields, remote-failure handling, and model-file structure using in-memory substitutes for serial, GPIO, and SSH. They do not access hardware.

## Desktop setup

Create and activate a Python environment, then install the dependencies:

```bash
python -m venv .venv
python -m pip install -r requirements-desktop.txt
```

Tkinter must be available in the Python installation. Copy `desktop/config.example.ini` to `desktop/config.local.ini` and set the instrument username, hostname, remote controller path, and acquisition directory. Optional password authentication reads `LAB_PI_PASSWORD`; Paramiko can also use SSH keys.

After configuring the instrument:

```bash
python desktop/control_gui.py
```

## Hardware setup and limitations

Install `requirements-raspberry-pi.txt` on the Raspberry Pi. Obtain the UAI spectrometer SDK for the device and set `LAB_SPECTROMETER_LIBRARY` to its library path. The vendor binary is not included.

The STM32 sources target STM32F103C8T6. Review the wiring, timer configuration, and manually edited firmware before building with a compatible STM32 toolchain. The saved CubeMX configuration may not include all source changes. The contributed Arduino variant uses a separate protocol.

See [hardware and protocol notes](docs/hardware-and-protocol.md) for pin assignments, command fields, and known timing and cancellation limitations. Firmware compilation, graphical operation, and live acquisition have not been verified for this release.

The Xcos model is a thermal feedback example with heater, valve, sensor, and PID blocks. It has been inspected structurally but not simulated, and does not validate pump mass control.

## Attribution

The custom instrument code, contributed Arduino variant, and vendor drivers have distinct provenance. See [attribution and source notes](docs/attribution-and-curation.md) and the [source ledger](docs/provenance.json).

STMicroelectronics and Arm/CMSIS license notices remain with their files. No repository-wide license has been assigned to the custom code or model.
