#!/usr/bin/env python3
"""Print the control interface URL from hardware.yaml for run.bat."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from backend.config import get_config
    server = get_config()["server"]
    host = str(server.get("host", "127.0.0.1"))
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    print(f"http://{host}:{int(server.get('port', 8000))}")
except Exception:
    print("http://127.0.0.1:8000")
