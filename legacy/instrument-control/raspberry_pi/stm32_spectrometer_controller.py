# 1. ───────────────────────────── Imports and module constants ─────────────────────────────
import serial, sys, json, os, time, shutil, RPi.GPIO as GPIO
from datetime import datetime
from threading import Thread
from ctypes import *


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
        # ---- 1. Parse the on-time value ----
        on_int = int(on)  # Integer milliseconds
        on_dec = int(
            round((on - on_int) * 100)
        )  # Encode hundredths of a millisecond in three digits

        # Original fractional carry guard; see the protocol notes
        if on_dec == 1000:
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

            ts = datetime.now().strftime("%Y%m%d-%H%M%S%f")[:-5]
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
                time.sleep(0.0005)
            time.sleep(0.002)
            GPIO.output(self.Pin, GPIO.HIGH)
            time.sleep(0.010)
            GPIO.output(self.Pin, GPIO.LOW)
            GPIO.setup(self.Pin, GPIO.IN)
            GPIO.cleanup()

    # 5. ───────────────────────────── Main acquisition routine ────────────────────────────
    def run(self):
        # 5-1  UART
        if not self.connect_arduino():
            return

        # 5-2  Parse GUI or CLI parameters from JSON
        if len(sys.argv) < 2:
            print("Need JSON parameter")
            return
        p = json.loads(sys.argv[1])
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
        est_to_ms = (p["Ontime"] + p["Offtime"]) * p[
            "Cycle"
        ] + 2000  # Duration calculated in milliseconds
        est_to_sec = int(
            round(est_to_ms)
        )  # Original conversion retained; see the timeout-unit caveat
        est_to_sec = max(1, min(est_to_sec, 9999))  # Clamp to the supported four-digit field

        cond = self.send_condition(
            p["Ontime"], p["Offtime"], p["Cycle"], p["Voltage"], est_to_sec
        )  # Transmit the timeout field interpreted as seconds
        time.sleep(2)
        print("[UART]", cond)

        # 5-7  Prepare and start the acquisition and pulse threads
        self.signal = [None] * p["Loopnum"]
        label = self.sol_label(
            p["Conductivity"], p["Metal1"], p["Metal2"], p["Metal3"], p["Metal4"], p["Metal5"]
        )

        t_spec = Thread(
            target=self.Spec_thread,
            args=(
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
        t_pulse = Thread(target=self.Pulse_thread, args=(p["Loopnum"],))
        t_spec.start()
        t_pulse.start()
        t_spec.join()
        t_pulse.join()

        # 5-8  Finish by stopping STM32 pulse generation
        self.Arduino_ser.write(b"stop\n")
        print("[DONE] all loops finished")


# 6. ────────────────────────────  Script entry point  ─────────────────────────────
if __name__ == "__main__":
    Rpi_STM32_Control().run()
