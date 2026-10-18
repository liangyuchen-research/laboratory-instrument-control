# 1. ───────────────────────────── Imports and module constants ─────────────────────────────
import json
import math
import os
import sys
import time

import serial
import RPi.GPIO as GPIO
from datetime import datetime
from threading import Thread
from queue import Queue
from ctypes import CDLL, c_float, c_int, c_short, c_void_p, pointer


def calculate_timeout_seconds(on_ms, off_ms, cycles, integration_ms, interval_ms):
    """Budget the STM32 trigger watchdog in whole seconds, without shortening it.

    Each rising trigger resets the firmware watchdog. Cover the encoded pulse
    train, the initial two-second host delay, and subsequent acquisition gaps,
    retaining the original two-second allowance for scheduling and file I/O.
    """
    values = (on_ms, off_ms, cycles, integration_ms, interval_ms)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in values):
        raise ValueError("Timing parameters must be finite numbers")
    if not 0.01 <= on_ms <= 999.99:
        raise ValueError("On-time must be between 0.01 and 999.99 ms")
    for name, value in (("Off-time", off_ms), ("Cycle count", cycles)):
        if int(value) != value or not 1 <= value <= 9999:
            raise ValueError(f"{name} must be an integer from 1 to 9999")
    if int(integration_ms) != integration_ms or integration_ms < 1 or interval_ms < 0:
        raise ValueError("Integration time must be a positive integer and interval nonnegative")

    # Match the command encoder's hundredth-millisecond rounding, including carry.
    on_integer = int(on_ms)
    on_hundredths = on_integer * 100 + int(round((on_ms - on_integer) * 100))
    pulse_ms = (on_hundredths / 100 + off_ms) * cycles
    initial_trigger_gap_ms = 2000 + interval_ms
    subsequent_trigger_gap_ms = integration_ms + interval_ms
    budget_ms = max(pulse_ms, initial_trigger_gap_ms, subsequent_trigger_gap_ms) + 2000
    if budget_ms > 9999 * 1000:
        raise ValueError("Required trigger-watchdog timeout exceeds the 9999-second protocol limit")
    return max(1, math.ceil(budget_ms / 1000))


# 2. ────────────────────────────── Main controller ────────────────────────────────
class Rpi_STM32_Control:
    """Control STM32 pulser + UAI spectrometer (Mode 1: BG + Raw, Mode 2: BG-sub)."""

    # 2-1  Initial parameters
    def __init__(self):
        self.arduino_port = os.environ.get("LAB_SERIAL_PORT", "/dev/serial0")  # Pi ↔ STM32 UART
        self.Pin = int(os.environ.get("LAB_TRIGGER_PIN", "16"))  # BOARD16 (GPIO23) → STM32 PB10
        self.stop_file = os.path.join(os.path.dirname(__file__), "stop_rpi_control")
        self.running = True
        self.signal = []  # handshake list for threads
        self.worker_errors = Queue()

    # 2-2  UART connection
    def connect_arduino(self):
        if hasattr(self, "Arduino_ser") and self.Arduino_ser.is_open:
            return True
        try:
            self.Arduino_ser = serial.Serial(
                self.arduino_port,
                int(os.environ.get("LAB_SERIAL_BAUD", "9600")),
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_TWO,
                bytesize=serial.EIGHTBITS,
                timeout=1,
            )
            time.sleep(2)
            return True
        except serial.SerialException as e:
            print("[UART-ERR]", e)
            return False

    # 2-3  Encode and send the condition command
    def send_condition(self, on, off, cyc, volt, timeout):
        """
        Encode timing, voltage, cycle count, and timeout parameters.
        The first six payload digits encode integer milliseconds and hundredths of a millisecond.
        """
        fields = {"on": on, "off": off, "cycles": cyc, "voltage": volt, "timeout": timeout}
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in fields.values()):
            raise ValueError("Pulse parameters must be finite numbers")
        if not 0.01 <= on <= 999.99:
            raise ValueError("On-time must be between 0.01 and 999.99 ms")
        for name, value in (("off", off), ("cycles", cyc), ("timeout", timeout)):
            if int(value) != value or not 1 <= value <= 9999:
                raise ValueError(f"{name} must be an integer from 1 to 9999")
        if int(volt) != volt or not 0 <= volt <= 9999:
            raise ValueError("Voltage must be an integer from 0 to 9999")

        # ---- 1. Parse the on-time value ----
        on_int = int(on)  # Integer milliseconds
        on_dec = int(
            round((on - on_int) * 100)
        )  # Encode hundredths of a millisecond in three digits

        # Normalize rounding at a millisecond boundary without widening the field.
        if on_dec == 100:
            on_int += 1
            on_dec = 0

        # ---- 2. Encode off-time as integer milliseconds ----
        off_int = int(off)

        # ---- 3. Build the command string ----
        cond = (
            "condition"
            f"{on_int:03d}{on_dec:03d}"  # Six digits for on-time
            f"{off_int:04d}"  # Four digits for off-time
            f"{int(cyc):04d}"  # Four digits for cycle count
            f"{int(volt):04d}"  # Four digits for voltage
            f"{int(timeout):04d}"  # Four digits for timeout in seconds
        )

        # ---- 4. Send the command without a preceding stop command ----
        time.sleep(2)  # Preserve the original settling delay
        self.Arduino_ser.write((cond + "\n").encode())

        # ---- 5. Trigger the STM32 through GPIO23 ----
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self.Pin, GPIO.OUT)
        GPIO.output(self.Pin, GPIO.HIGH)
        time.sleep(0.01)
        GPIO.output(self.Pin, GPIO.LOW)

        return cond  # Return the transmitted command for diagnostics

    # 2-4  Spectrometer connection
    def Spectrometer_Connection(self):
        dll_path = os.environ.get("LAB_SPECTROMETER_LIBRARY")
        if not dll_path:
            raise RuntimeError("Set LAB_SPECTROMETER_LIBRARY to the vendor SDK library path.")
        dll = CDLL(dll_path)

        # Enumerate connected devices
        typelist = c_int(0)
        dll.UAI_SpectrometerGetDeviceList(pointer(typelist), None)
        VIDPID = (c_int * (typelist.value * 2))()
        dll.UAI_SpectrometerGetDeviceList(pointer(typelist), pointer(VIDPID))

        hand = c_void_p(0)
        frame_size = c_short()
        for i in range(typelist.value):
            dev_cnt = c_int()
            dll.DLI_SpectrometerGetDeviceAmount(VIDPID[i * 2], VIDPID[i * 2 + 1], pointer(dev_cnt))
            if dev_cnt.value == 0:
                continue
            if (
                dll.UAI_SpectrometerOpen(c_int(0), pointer(hand), VIDPID[i * 2], VIDPID[i * 2 + 1])
                == 0
            ):
                break
        if not hand:
            raise RuntimeError("No spectrometer found")

        dll.UAI_SpectromoduleGetFrameSize(hand, pointer(frame_size))
        SD_lambda = (c_float * frame_size.value)()
        dll.UAI_SpectrometerWavelengthAcquire(hand, pointer(SD_lambda))
        buffer = (c_float * frame_size.value)()
        return hand, dll, buffer, frame_size, SD_lambda

    # 2-5  Acquire one background spectrum
    def Spectrum_background(self, IT, interval_ms):
        time.sleep(interval_ms / 1000)
        self.dll.UAI_SpectrometerDataOneshot(self.hand, IT * 1000, pointer(self.buffer), 1)
        return list(self.buffer)

    # 2-6  Filename helper
    def sol_label(self, *vals):
        return "_".join(str(v) for v in vals)

    # 3. ────────────────────── Spectral acquisition thread ──────────────────────
    def Spec_thread(self, ON, OFF, cyc, IT, interval, loopnum, volt, path, label):
        avg = 1
        for i in range(loopnum):
            if not self.running:
                return
            time.sleep(interval / 1000)
            self.signal[i] = "S"  # Allow the pulse thread to proceed

            ts = datetime.now().strftime("%Y%m%d-%H%M%S%f")
            fname = f"{path}/{ts}_{volt}_{ON}_{OFF}_{cyc}_{label}.txt"

            self.dll.UAI_SpectrometerDataOneshot(self.hand, IT * 1000, pointer(self.buffer), avg)
            with open(fname, "w") as f:
                for k in range(self.frame_size.value):
                    wl = f"{self.SD_lambda[k]:.3f}"
                    if self.sel_mode == 1:  # Raw
                        inten = f"{self.buffer[k]:.8f}"
                    else:  # Mode 2 → Raw-BG
                        inten = f"{(self.buffer[k]-self.background[k]):.8f}"
                    f.write(f"{wl}\t{inten}\n")

    # 4. ────────────────────── STM32 pulse thread ─────────────────────
    def Pulse_thread(self, loopnum):
        for i in range(loopnum):
            if not self.running:
                return
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(self.Pin, GPIO.OUT)
            while self.signal[i] != "S":
                if not self.running:
                    return
                time.sleep(0.0005)
            time.sleep(0.002)
            GPIO.output(self.Pin, GPIO.HIGH)
            time.sleep(0.010)
            GPIO.output(self.Pin, GPIO.LOW)
            GPIO.setup(self.Pin, GPIO.IN)
            GPIO.cleanup()

    # 5. ───────────────────────────── Main acquisition routine ────────────────────────────
    def _run_worker(self, target, *args):
        try:
            target(*args)
        except Exception as exc:
            self.worker_errors.put(exc)
            self.running = False

    def run(self):
        if len(sys.argv) != 2:
            raise ValueError("Supply one JSON object containing acquisition parameters")
        p = json.loads(sys.argv[1])
        if not isinstance(p, dict):
            raise ValueError("Acquisition parameters must be a JSON object")
        required = ("Selected Mode", "Ontime", "Offtime", "Cycle", "Voltage",
                    "Integration Time", "Spectra Interval", "Loopnum", "Conductivity",
                    "Metal1", "Metal2", "Metal3", "Metal4", "Metal5")
        for key in required:
            value = p.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
        if p["Selected Mode"] not in (1, 2):
            raise ValueError("Selected Mode must be 1 (raw) or 2 (background-subtracted)")
        for key in ("Loopnum", "Integration Time"):
            if int(p[key]) != p[key] or p[key] < 1:
                raise ValueError(f"{key} must be a positive integer")
        if p["Spectra Interval"] < 0:
            raise ValueError("Spectra Interval cannot be negative")
        p["Loopnum"] = int(p["Loopnum"])
        p["Integration Time"] = int(p["Integration Time"])
        self.timeout_seconds = calculate_timeout_seconds(
            p["Ontime"], p["Offtime"], p["Cycle"],
            p["Integration Time"], p["Spectra Interval"]
        )
        if not self.connect_arduino():
            raise RuntimeError("Could not connect to the STM32 UART")
        try:
            self._acquire(p)
        finally:
            self.running = False
            try:
                if self.Arduino_ser.is_open:
                    self.Arduino_ser.write(b"stop\n")
            finally:
                self.Arduino_ser.close()
                GPIO.cleanup()
        print("[DONE] all loops finished")

    def _acquire(self, p):
        self.sel_mode = p["Selected Mode"]  # Mode 1 or mode 2

        # 5-3  Connect to the spectrometer
        self.hand, self.dll, self.buffer, self.frame_size, self.SD_lambda = (
            self.Spectrometer_Connection()
        )

        # 5-4  Create the acquisition directory
        base = os.environ.get(
            "LAB_DATA_DIR", os.path.join(os.path.dirname(__file__), "acquisitions")
        )
        day = os.path.join(base, datetime.now().strftime("%Y%m%d"))
        path = os.path.join(day, datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(path, exist_ok=True)

        # 5-5  Acquire a background spectrum for both modes
        self.background = self.Spectrum_background(p["Integration Time"], p["Spectra Interval"])
        bg_file = f'{path}/{datetime.now().strftime("%Y%m%d-%H%M%S")}_BG.txt'
        with open(bg_file, "w") as f:
            for k in range(self.frame_size.value):
                f.write(f"{self.SD_lambda[k]:.3f}\t{self.background[k]:.8f}\n")

        # 5-6  Encode parameters and trigger the STM32
        cond = self.send_condition(
            p["Ontime"], p["Offtime"], p["Cycle"], p["Voltage"], self.timeout_seconds
        )  # Validated whole-second trigger-watchdog budget
        time.sleep(2)
        print("[UART]", cond)

        # 5-7  Prepare and start the acquisition and pulse threads
        self.signal = [None] * p["Loopnum"]
        label = self.sol_label(
            p["Conductivity"], p["Metal1"], p["Metal2"], p["Metal3"], p["Metal4"], p["Metal5"]
        )

        t_spec = Thread(
            target=self._run_worker,
            args=(
                self.Spec_thread,
                p["Ontime"],
                p["Offtime"],
                p["Cycle"],
                p["Integration Time"],
                p["Spectra Interval"],
                p["Loopnum"],
                p["Voltage"],
                path,
                label,
            ),
        )
        t_pulse = Thread(target=self._run_worker, args=(self.Pulse_thread, p["Loopnum"],))
        t_spec.start()
        t_pulse.start()
        t_spec.join()
        t_pulse.join()
        if not self.worker_errors.empty():
            raise RuntimeError("Acquisition worker failed") from self.worker_errors.get()

        # run() sends the final stop command and closes the UART in its finally block.


# 6. ────────────────────────────  Script entry point  ─────────────────────────────
if __name__ == "__main__":
    Rpi_STM32_Control().run()
