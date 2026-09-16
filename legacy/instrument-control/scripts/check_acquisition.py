"""Regression checks for legacy acquisition using temporary data and fake I/O."""

import ast
import io
import json
import math
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))
from spectral_data import SpectralDataReader


def controller_methods():
    path = ROOT / "raspberry_pi/stm32_spectrometer_controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    names = {"_run_worker", "Pulse_thread", "run"}
    selected = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    selected.insert(0, next(node for node in tree.body
                           if isinstance(node, ast.FunctionDef) and node.name == "calculate_timeout_seconds"))
    gpio = SimpleNamespace(BOARD=1, OUT=1, IN=0, HIGH=1, LOW=0,
                           setmode=lambda *_: None, setup=lambda *_: None,
                           output=lambda *_: None, cleanup=lambda: None)
    namespace = {"GPIO": gpio, "time": time, "sys": sys, "json": json, "math": math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), "exec"), namespace)
    return namespace


class AcquisitionChecks(unittest.TestCase):
    @staticmethod
    def parameters():
        return {"Selected Mode": 1, "Ontime": 1.25, "Offtime": 12, "Cycle": 34,
                "Voltage": 30, "Integration Time": 10, "Spectra Interval": 20,
                "Loopnum": 1, "Conductivity": 0, **{f"Metal{i}": 0 for i in range(1, 6)}}

    def test_uart_failure_is_reported_to_the_desktop(self):
        methods = controller_methods()
        controller = SimpleNamespace(connect_arduino=lambda: False)
        previous = sys.argv
        try:
            sys.argv = ["controller.py", json.dumps(self.parameters())]
            with self.assertRaisesRegex(RuntimeError, "Could not connect"):
                methods["run"](controller)
        finally:
            sys.argv = previous

    def test_acquisition_failure_stops_and_closes_the_uart(self):
        methods = controller_methods()
        events = []
        def fail(params):
            raise OSError("SDK unavailable")
        controller = SimpleNamespace(connect_arduino=lambda: True, _acquire=fail,
                                     Arduino_ser=SimpleNamespace(is_open=True,
                                         write=lambda data: events.append(data),
                                         close=lambda: events.append("close")))
        previous = sys.argv
        try:
            sys.argv = ["controller.py", json.dumps(self.parameters())]
            with self.assertRaisesRegex(OSError, "SDK unavailable"):
                methods["run"](controller)
        finally:
            sys.argv = previous
        self.assertEqual(events, [b"stop\n", "close"])
        self.assertFalse(controller.running)

    def test_unrepresentable_timeout_is_rejected_before_uart_connection(self):
        methods = controller_methods()
        events = []
        controller = SimpleNamespace(connect_arduino=lambda: events.append("connect"))
        parameters = self.parameters()
        parameters.update({"Ontime": 999.99, "Offtime": 9999, "Cycle": 9999})
        previous = sys.argv
        try:
            sys.argv = ["controller.py", json.dumps(parameters)]
            with self.assertRaisesRegex(ValueError, "9999-second"):
                methods["run"](controller)
        finally:
            sys.argv = previous
        self.assertEqual(events, [])

    def test_current_export_format_roundtrip_and_background_handling(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            names = ["20260917-120000123456_30_1.25_12_34_100_1_2_3_4_5.txt",
                     "20260917-120000223456_30_1.25_12_34_100_1_2_3_4_5.txt"]
            for index, name in enumerate(names):
                (root / name).write_text(f"200\t{5 + index}\n201\t-2\n", encoding="utf-8")
            background = root / "20260917-120000_BG.txt"
            background.write_text("200\t1000\n201\t1000\n", encoding="utf-8")
            conditions, data, groups = SpectralDataReader(list(root.glob("*.txt"))).Data_transfer()
            self.assertEqual(conditions.shape, (2, 12))
            self.assertEqual(data.shape, (2, 2))
            self.assertEqual(data.iloc[0].tolist(), [5, -2])
            self.assertEqual(conditions["Time"].tolist(), [0, 0.1])
            self.assertEqual(conditions["Ontime"].tolist(), [1.25, 1.25])
            self.assertEqual(len(groups), 2)

    def test_incompatible_wavelength_grids_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = []
            for index, grid in enumerate(("200\t1\n201\t2", "200\t1\n202\t2")):
                path = root / f"20260917-12000{index}123456_30_1_1_1_0_0_0_0_0_0.txt"
                path.write_text(grid, encoding="utf-8")
                files.append(path)
            with self.assertRaisesRegex(ValueError, "Wavelength grid differs"):
                SpectralDataReader(files).Data_transfer()

    def test_worker_failure_releases_waiting_pulse_thread(self):
        methods = controller_methods()
        controller = SimpleNamespace(running=True, signal=[None], Pin=16, worker_errors=queue.Queue())
        thread = threading.Thread(target=methods["Pulse_thread"], args=(controller, 1))
        thread.start()
        def failed_acquisition():
            raise OSError("SDK acquisition failed")
        methods["_run_worker"](controller, failed_acquisition)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(controller.worker_errors.get_nowait(), OSError)


if __name__ == "__main__":
    unittest.main(verbosity=2)
