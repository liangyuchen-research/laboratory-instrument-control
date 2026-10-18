#!/usr/bin/env python3
"""Check a Python interpreter for the Windows launcher.

A separate script avoids nested cmd.exe quoting when testing the interpreter
path and version. A Microsoft Store alias produces no interpreter-path output.

    python tools/probe.py            Print the interpreter path.
    python tools/probe.py --require  Exit 0 for Python >= 3.9, otherwise exit 1.
"""
import sys

MIN = (3, 9)

if "--require" in sys.argv:
    if sys.version_info[:2] < MIN:
        got = ".".join(str(n) for n in sys.version_info[:3])
        need = ".".join(str(n) for n in MIN)
        print(f"Python {got} was found; version {need} or later is required")
        sys.exit(1)
    print(sys.version.split()[0])
    sys.exit(0)

print(sys.executable)
