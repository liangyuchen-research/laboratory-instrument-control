"""Check remote failure handling without loading the GUI or opening SSH."""

import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import queue
import shlex
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_method(name):
    source = (ROOT / "desktop/control_gui_support.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    controller = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "RaspberryPiController")
    method = next(node for node in controller.body if isinstance(node, ast.FunctionDef)
                  and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"sys": sys, "shlex": shlex}
    exec(compile(module, "<offline-desktop-check>", "exec"), namespace)
    return namespace[name]


class RemoteAcquisitionChecks(unittest.TestCase):
    def test_nonzero_remote_exit_is_failure(self):
        sender = load_method("send_to_pi")
        for status in (0, 1, 127):
            with self.subTest(exit_status=status):
                events = []
                channel = SimpleNamespace(
                    get_pty=lambda: None,
                    exec_command=lambda command: events.append("command"),
                    exit_status_ready=lambda: True,
                    recv_ready=lambda: False,
                    recv_stderr_ready=lambda: False,
                    recv_exit_status=lambda: status,
                )
                client = SimpleNamespace(
                    set_missing_host_key_policy=lambda policy: None,
                    connect=lambda *args, **kwargs: events.append("connect"),
                    get_transport=lambda: SimpleNamespace(open_session=lambda: channel),
                    close=lambda: events.append("close"),
                )
                modules = {
                    "paramiko": SimpleNamespace(SSHClient=lambda: client, AutoAddPolicy=object),
                    "tkinter": SimpleNamespace(messagebox=SimpleNamespace(
                        showerror=lambda *args: events.append("error")
                    )),
                }
                controller = SimpleNamespace(pi_address="instrument.example",
                                             pi_user="operator", pi_password=None,
                                             pi_python_file="/opt/instrument/controller.py")
                with patch.dict(sys.modules, modules), redirect_stdout(io.StringIO()):
                    result = sender(controller, {"Ontime": 1.25})
                self.assertEqual(result, status == 0)
                self.assertEqual("error" in events, status != 0)
                self.assertEqual(events[-1], "close")

    def test_failed_acquisition_does_not_download_previous_results(self):
        worker = load_method("run_pi_commands")
        downloads = []
        controller = SimpleNamespace(
            values={}, stop=False, queue=queue.Queue(),
            file_path_list=["previous-acquisition.txt"],
            send_to_pi=lambda values: False,
            get_result_from_pi=lambda: downloads.append(True),
        )
        worker(controller)
        self.assertEqual(downloads, [])
        self.assertEqual(controller.file_path_list, [])
        self.assertEqual(controller.queue.get_nowait(), "thread_finished")

    def test_worker_reports_completion_after_unexpected_failure(self):
        worker = load_method("run_pi_commands")

        def fail(values):
            raise RuntimeError("test failure")

        controller = SimpleNamespace(values={}, queue=queue.Queue(), send_to_pi=fail)
        with self.assertRaises(RuntimeError):
            worker(controller)
        self.assertEqual(controller.queue.get_nowait(), "thread_finished")


if __name__ == "__main__":
    unittest.main(verbosity=2)
