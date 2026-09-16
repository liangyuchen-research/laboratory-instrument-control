"""Check the actual command encoder with in-memory serial/GPIO fakes only."""

from pathlib import Path
import ast
import math
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class FakeSerial:
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(message)


def main() -> None:
    source = (ROOT / "raspberry_pi/stm32_spectrometer_controller.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "send_condition"
    )
    # Compile only this function. The module's hardware imports and entry point never run.
    timeout_helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_timeout_seconds"
    )
    module = ast.Module(body=[timeout_helper, method], type_ignores=[])
    ast.fix_missing_locations(module)
    gpio_events = []
    gpio = SimpleNamespace(
        BOARD="BOARD",
        OUT="OUT",
        HIGH=1,
        LOW=0,
        setmode=lambda mode: None,
        setup=lambda pin, mode: None,
        output=lambda pin, value: gpio_events.append((pin, value)),
    )
    namespace = {"GPIO": gpio, "math": math, "time": SimpleNamespace(sleep=lambda seconds: None)}
    exec(compile(module, "<mocked-command-encoder>", "exec"), namespace)
    controller = SimpleNamespace(Arduino_ser=FakeSerial(), Pin=16)
    command = namespace["send_condition"](controller, 1.25, 12, 34, 56, 78)

    assert command == "condition0010250012003400560078"
    assert controller.Arduino_ser.messages == [b"condition0010250012003400560078\n"]
    assert gpio_events == [(16, 1), (16, 0)]
    # Independent receiver-side field positions from the supplied STM32 parser.
    assert float(command[9:12]) + float(command[12:15]) / 100 == 1.25
    assert int(command[15:19]) == 12
    assert int(command[19:23]) == 34
    assert int(command[23:27]) == 56
    assert int(command[27:31]) == 78
    for invalid in (float("nan"), float("inf"), 1000, -1, True):
        try:
            namespace["send_condition"](controller, invalid, 12, 34, 56, 78)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid on-time was accepted: {invalid}")
    assert len(controller.Arduino_ser.messages) == 1
    rounded = namespace["send_condition"](controller, 1.999, 12, 34, 56, 78)
    assert rounded[9:15] == "002000"
    timeout = namespace["calculate_timeout_seconds"]
    assert timeout(1.25, 12, 34, 10, 20) == 5
    assert timeout(10, 10, 100, 100, 0) == 4
    assert timeout(1.996, 1, 3334, 10, 0) == 13
    assert timeout(0.01, 1, 1, 10, 0.001) == 5
    assert timeout(0.01, 1, 1, 5000, 6000) == 13
    assert timeout(999, 8998, 1000, 10, 0) == 9999
    for values in ((999.01, 8998, 1000, 10, 0),
                   (999.99, 9999, 9999, 10, 0),
                   (1, 1, 1, 10, float("inf")),
                   (1, 1, 1, 10, -1), (1, 1.5, 1, 10, 0)):
        try:
            timeout(*values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid or unrepresentable timing was accepted: {values}")
    budget = timeout(1.25, 12, 34, 10, 20)
    encoded = namespace["send_condition"](controller, 1.25, 12, 34, 56, budget)
    assert int(encoded[27:31]) == 5
    assert int(encoded[27:31]) * 1000 >= (1.25 + 12) * 34 + 2000
    print("PASS: timeout conversion covers pulse and acquisition gaps, rounds upward, and rejects overflow.")
    print("PASS: actual Python encoder matches the documented STM32 field layout.")
    print("Only in-memory serial/GPIO fakes were used; no hardware or network access occurred.")


if __name__ == "__main__":
    main()
