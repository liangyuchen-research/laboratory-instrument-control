"""Compile the preserved STM32F103C8 firmware with Arm GNU Toolchain.

This command builds ELF, HEX and BIN files only. It never discovers a board,
opens a serial port or flashes hardware. Use --toolchain to select a directory
containing arm-none-eabi-gcc, or put the toolchain's bin directory on PATH.
"""

import argparse
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", type=Path, help="Arm GNU Toolchain bin directory")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    args = parser.parse_args()
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)

    def tool(name):
        candidate = args.toolchain / f"arm-none-eabi-{name}" if args.toolchain else f"arm-none-eabi-{name}"
        found = shutil.which(str(candidate))
        if not found:
            parser.error(f"Cannot find {candidate}; install Arm GNU Toolchain or use --toolchain")
        return found

    gcc, objcopy, size = tool("gcc"), tool("objcopy"), tool("size")
    subprocess.run([gcc, "--version"], check=True)
    includes = [ROOT / "Core/Inc", ROOT / "Drivers/STM32F1xx_HAL_Driver/Inc",
                ROOT / "Drivers/STM32F1xx_HAL_Driver/Inc/Legacy",
                ROOT / "Drivers/CMSIS/Device/ST/STM32F1xx/Include", ROOT / "Drivers/CMSIS/Include"]
    flags = ["-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-DUSE_HAL_DRIVER",
             "-DSTM32F103xB", "-Os", "-g3", "-ffunction-sections", "-fdata-sections", "-Wall"]
    include_flags = [f"-I{path}" for path in includes]
    sources = sorted((ROOT / "Core/Src").glob("*.c")) + sorted((ROOT / "Drivers/STM32F1xx_HAL_Driver/Src").glob("*.c"))
    sources += sorted((ROOT / "Core/Startup").glob("*.s"))
    objects = []
    for source in sources:
        target = build / (source.stem + ".o")
        language_flags = ["-std=gnu11"] if source.suffix == ".c" else ["-x", "assembler-with-cpp"]
        subprocess.run([gcc, *flags, *include_flags, *language_flags, "-c", str(source), "-o", str(target)], check=True)
        objects.append(str(target))

    elf = build / "instrument-control.elf"
    # CubeIDE emits READONLY section annotations unsupported by older GNU ld.
    # Input section flags already mark these tables read-only. Keep the archived
    # linker script untouched and write a compatible build-directory copy.
    linker = build / "STM32F103C8TX_FLASH.ld"
    linker.write_text((ROOT / "STM32F103C8TX_FLASH.ld").read_text(encoding="utf-8").replace("(READONLY)", ""), encoding="utf-8")
    subprocess.run([gcc, *flags, "--specs=nano.specs", "--specs=nosys.specs", *objects,
                    f"-T{linker}", "-Wl,--gc-sections",
                    f"-Wl,-Map={build / 'instrument-control.map'}",
                    "-Wl,--start-group", "-lc", "-lm", "-Wl,--end-group", "-o", str(elf)], check=True)
    for fmt, suffix in (("ihex", ".hex"), ("binary", ".bin")):
        subprocess.run([objcopy, "-O", fmt, str(elf), str(elf.with_suffix(suffix))], check=True)
    subprocess.run([size, str(elf)], check=True)
    print(f"Built firmware in {build}. No hardware was accessed.")


if __name__ == "__main__":
    main()
