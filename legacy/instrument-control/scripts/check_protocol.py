"""Check the actual command encoder with in-memory serial/GPIO fakes only."""

from pathlib import Path
import ast
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
    module = ast.Module(body=[method], type_ignores=[])
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
    namespace = {"GPIO": gpio, "time": SimpleNamespace(sleep=lambda seconds: None)}
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
    print("PASS: actual Python encoder matches the documented STM32 field layout.")
    print("Only in-memory serial/GPIO fakes were used; no hardware or network access occurred.")


if __name__ == "__main__":
    main()
