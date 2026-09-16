"""Regression checks for API validation and serial response handling.

Run with python -m unittest tools.test_regressions. Tests use fakes and an
in-process HTTP transport; they do not start the serial connection.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.registry import Context, FunctionError, Param
from backend.transport import Link, LinkError, _default_terminators


class NumericValidationTests(unittest.TestCase):
    def test_non_finite_parameters_are_rejected(self):
        for kind in ("number", "integer"):
            for value in ("nan", "inf", "-inf", "1e999"):
                with self.subTest(kind=kind, value=value):
                    with self.assertRaises(FunctionError):
                        Param("target", kind).coerce(value)

    def test_finite_values_retain_conversion_and_bounds(self):
        self.assertEqual(Param("mass", minimum=0, maximum=10).coerce("2.5"), 2.5)
        self.assertEqual(Param("motor", "integer").coerce("3"), 3)
        with self.assertRaises(FunctionError):
            Param("motor", "integer").coerce(3.2)
        with self.assertRaises(FunctionError):
            Param("mass", maximum=10).coerce(11)


class TransportTests(unittest.TestCase):
    def test_incomplete_response_does_not_report_success(self):
        link = Link()
        link.connected = True
        fake = Mock()
        fake.write.side_effect = lambda _: link._handle_line("READY,test")
        link._sim = fake
        with self.assertRaisesRegex(LinkError, "timed out"):
            link.send("V,3,20", timeout=0.001)
        self.assertIsNone(link._pending)

    def test_modbus_set_accepts_firmware_configuration_reply(self):
        self.assertIn("CFG,", _default_terminators("W,SET,1,3,0,1"))
        link = Link()
        link.connected = True
        fake = Mock()
        fake.write.side_effect = lambda _: link._handle_line("CFG,addr=1,fc=3,reg=0,fmt=1")
        link._sim = fake
        self.assertEqual(link.send("W,SET,1,3,0,1", timeout=0.01),
                         ["CFG,addr=1,fc=3,reg=0,fmt=1"])

    def test_help_terminator_matches_stripped_serial_lines(self):
        line = "  W,CFG / W,HELP        Show settings / this help".strip()
        self.assertTrue(any(line.startswith(t) for t in _default_terminators("W,HELP")))

    def test_non_finite_measurements_do_not_enter_state(self):
        link = Link()
        link._apply_weight("WEIGHT,2.5,RAW,625,ST,1")
        previous = dict(link.snapshot()["scale"])
        for value in ("nan", "inf", "-inf"):
            link._apply_weight(f"WEIGHT,{value},RAW,0,ST,1")
        self.assertEqual(link.snapshot()["scale"], previous)


class ApiTests(unittest.TestCase):
    def test_malformed_json_never_invokes_an_operation(self):
        from backend.main import app

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                return await client.post("/fn/pump.init", content="{")

        with patch("backend.main.registry.get") as get_operation:
            response = asyncio.run(request())
        self.assertEqual(response.status_code, 400)
        get_operation.assert_not_called()

    def test_diagnostics_reject_active_pumps_and_cleaning(self):
        from backend.functions.scale import diagnose
        for state in ({"cleaning": True, "motors": {}},
                      {"cleaning": False, "motors": {"A": {"state": "run"}}},
                      {"cleaning": False, "motors": {"A": {"state": "wait"}}}):
            with self.subTest(state=state):
                link = Mock()
                link.snapshot.return_value = state
                with self.assertRaises(FunctionError):
                    diagnose(Context(link=link, config={}), "scan")
                link.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
