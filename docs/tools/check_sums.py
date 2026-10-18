"""Confirm that the old version will be used and the file cannot be changed (SHA256SUMS.txt ) are still exactly the same as before.

    .\\pc\\runtime\\python\\python.exe -B .\\docs\\tools\\check_sums.py
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
changed, listed = [], 0
for line in (ROOT / 'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    digest, name = line.split(None, 1)
    name = name.lstrip('*').strip()
    listed += 1
    path = ROOT / name
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest.lower():
        changed.append(name)
print(f'List {listed} files; altered or missing:{", ".join(changed) if changed else "None"}')
sys.exit(1 if changed else 0)
