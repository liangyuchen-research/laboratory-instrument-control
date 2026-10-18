# Nano pulse output firmware 1.2.1 (original LCD arrangement) 

This is based on the current 1.1.0 Modbus Firmware-based independent version; retains the original output-disabled version. applicable tradition Nano ATmega328P/16 MHz,protocol version 1, RS485 address 2, 9600 8N1.D2=#RE, D3=DE, D12=trigger, D13=pulse; LCD for I2C 0x27/20×4.with the old `flash.ino` of ASCII `mode1start` incompatible.

Open the same directory `nano_rs485_v2_pulse.ino` must be retained `ControlCore.h`.This version is fixed `HARD_OUTPUT_ENABLE=1`,status register firmware for `0x0121` (1.2.1) ,`outputs_allowed=true`.has been burned on site 1.2.0 will not be changed by modifying the file; it will only be switched after explicit programming.

LCD 20×4 The earliest recovery `flash.ino` fixed arrangement of: No. 1 OK `POWER ON/OFF` and IT,No. 2 OK OT and SI,No. 3 OK FT and Vt,No. 4 OK CY and LP.OT/FT is displayed accurately to 0.1 ms.These values come from Nano Applied parameters; displayed when not synchronized `--`.`POWER ON*`/`POWER OFF*` yes RPi recent 5 within seconds HSPY Run/Stop is not the actual electrode voltage measurement; The power display of`F` express Nano fault,`R` express RUNNING.Actual pulse train RUNNING stop when LCD I2C updated to avoid disturbing the schedule; in ARMED/WAIT_TRIGGER Power readback can still be updated.

2026-09-23 has been RPi `parameter.json` of `Voltage` and HSPY The setting value is changed to **100 V**,when reading back HSPY still OFF, actual output 0 V/0 A.VSET rows only show Nano Accepted RPi metadata,It cannot be inferred from this HSPY Voltage has been applied. The upper computer must be rechecked before formal work. HSPY setting and readback.

## Pulse condition and stop

The host computer must establish a non-zero session, Submit and read back the valid parameters completely, keeping each 2 Update the heartbeat within seconds, and then use the same job ID/revision in order ARM, START.D12 must be sampled first LOW,New ones appear later HIGH edge. each loop requires new trigger;D13 according to on/off Time output, return when work is completed DONE and keep LOW.

The allowed range is ON/OFF each 1–1000 ms, 100 µs scale,`cycles × loops` most 10000, has the longest scheduled pulse time in the entire group 120 seconds. Maximum triggers per wait 30 seconds, the entire START Working period 120 Second.STOP Can be revoked in any state D13 output; losing heartbeat, triggering timeout or work timeout will cause The status ” LOW and the fault is latched. main loop watchdog for 2 seconds. These are procedural limitations and do not equate to electrical or physical safety certifications.

**D13 LOW and status returns only reflect Nano logic, does not prove driver,MOSFET Or the electrode is powered off.** Nano Not in control HSPY power supply;RPi Must be delivered independently OFF and confirm the readback. original In addition to bootloader/reset stage may use D13 has transient state and must be 24 V/is measured under high-voltage load, and the preset disable is ensured by hardware.

## Build and verify

Available Arduino IDE select Arduino Nano, actually used ATmega328P or Old Bootloader,as well as Wire and LiquidCrystal_I2C 2.0.0.It can also be found in the official AVR tool chain and library-dependent environment execution root directory offline compiler:

```bash
python3 deployment/build_nano_avr.py --pulse \
  --gcc /path/to/avr-toolchain --dependencies /path/to/arduino-dependencies \
  --sketch firmware/nano_rs485_v2_pulse --out /tmp/nano-pulse-build
```

product is `nano_rs485_v2.pulse.hex`,Contains only application,Does not contain bootloader.compile and simulated GPIO test will not write Nano.keep first 24 V/High voltage load is disconnected, read-only query after programming, confirm `firmware=1.2.1`, `outputs_allowed=true`, D13 LOW,Then measure with an oscilloscope D13 of ON/OFF time, times,D12 card high,STOP, Heartbeat timeout and reset Transient.

is offline this time AVR compiled successfully:Flash 15264/30720 bytes,static SRAM 1061/2048 bytes,application HEX SHA256 `cdb4d9d7137f451d00f1780902e2cf9fb52cd85369290fb5fce9db686e0c24ad`.single gear and divided gears sketch compiled HEX ; no physical waveform or high voltage testing has been done.

RPi Old version `RS232_pi.py` This firmware cannot be started; it still sends `ADDR:01:mode1start`.RPi of `nano_verify.py` has been updated and can be parsed 1.2.0/1.2.1.After programming and when the high voltage load is disconnected,Executable: 

```bash
python3 -B /home/pi/Desktop/Plasmic/nano_lcd_v1_1/rpi_tools/pulse_prepare.py \
  --app-dir /home/pi/Desktop/Plasmic/app
```

it requires HSPY OFF/output 0 V/0 A and Nano 1.2.x is idle and D13 LOW,will be established session, synchronous `parameter.json`, Read back completely and report LCD `POWER OFF` Snapshot; Not sending ARM/START, High voltage is not turned on. Existing read-only spectroscopy interfaces also require Nano output is locked and cannot be directly used as the official high-voltage spectroscopy working service of this version.
