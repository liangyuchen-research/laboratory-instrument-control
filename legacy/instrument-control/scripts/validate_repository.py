"""Validate source and model structure without importing hardware dependencies."""

from pathlib import Path
import ast
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u3400-\u9fff]")
SKIP = {".git", ".venv", "__pycache__", "outputs", "acquisitions"}


def main() -> None:
    source_files = 0
    python_files = 0
    for file in ROOT.rglob("*"):
        relative = file.relative_to(ROOT)
        if any(part in SKIP for part in relative.parts) or not file.is_file():
            continue
        assert not CJK.search(str(relative)), relative
        assert file.suffix not in {".exe", ".so", ".dll", ".elf", ".o", ".pyc"}, relative
        source_files += 1
        if file.suffix == ".zcos":
            with zipfile.ZipFile(file) as model:
                assert model.testzip() is None
                content = model.read("content.xml").decode("utf-8")
                assert ET.fromstring(content).tag == "XcosDiagram"
                assert not CJK.search(content)
            continue
        text = file.read_text(encoding="utf-8-sig")
        assert not CJK.search(text), relative
        if file.suffix == ".py":
            ast.parse(text)
            python_files += 1

    ledger = json.loads((ROOT / "docs/provenance.json").read_text(encoding="utf-8"))
    unchanged = 0
    for record in ledger:
        if record["transformation"].startswith("Byte-for-byte"):
            target = ROOT / record["repository_path"]
            assert (
                hashlib.sha256(target.read_bytes()).hexdigest() == record["source_sha256"]
            ), target
            unchanged += 1
    assert (ROOT / "firmware/stm32/Drivers/STM32F1xx_HAL_Driver/LICENSE.txt").is_file()
    assert (ROOT / "firmware/stm32/Drivers/CMSIS/LICENSE.txt").is_file()
    assert (ROOT / "firmware/stm32/Drivers/CMSIS/Device/ST/STM32F1xx/LICENSE.txt").is_file()
    print(
        f"PASS: {source_files} files, {python_files} Python syntax checks, {unchanged} unchanged source/model hashes."
    )
    print("Vendor notices and Xcos ZIP/XML structure are present. Hardware was not accessed.")


if __name__ == "__main__":
    main()
