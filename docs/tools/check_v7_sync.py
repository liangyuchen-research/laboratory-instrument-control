"""examine pc\\vendor (Copied V7 pump program,pyserial, PyYAML) and pc\\web\\fonts Have you been passive?

    python docs\\tools\\check_v7_sync.py                  Each file pair MANIFEST.sha256; More, less, and shouldn’t be there The same goes for the
    python docs\\tools\\check_v7_sync.py --v7 [folder]    Zaihe V7 (Default: Autonomous Laboratory on the desktopv7) 
    python docs\\tools\\check_v7_sync.py --write-manifest rewrite MANIFEST.sha256 (After copying, or deliberately following up V7 Do it later) 

read-only file (--write-manifest write only MANIFEST.sha256) .does not execute any copied programs and does not open any ports.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / 'pc' / 'vendor'
FONTS = ROOT / 'pc' / 'web' / 'fonts'
MANIFEST = VENDOR / 'MANIFEST.sha256'
V7_PARTS = ('backend', 'config', 'firmware', 'tools')   # what lies under pc\vendor\v7, as in the V7 folder


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listed():
    """Every file the manifest is about: (name relative to pc\\, path)."""
    rows = []
    for base in (VENDOR, FONTS):
        if base.is_dir():
            rows += [(item.relative_to(ROOT / 'pc').as_posix(), item) for item in sorted(base.rglob('*'))
                     if item.is_file() and item != MANIFEST]
    return rows


def write_manifest():
    lines = ['%s  %d  %s' % (digest(path), path.stat().st_size, name) for name, path in listed()]
    MANIFEST.write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
    print('has been written %s: %d files' % (MANIFEST.relative_to(ROOT), len(lines)))
    return 0


def check_manifest():
    problems = []
    try:
        wanted = {}
        for line in MANIFEST.read_text(encoding='utf-8').splitlines():
            sha, size, name = line.split('  ', 2)
            wanted[name] = (sha, int(size))
    except (OSError, ValueError) as exc:
        print('Can’t read MANIFEST.sha256: %s' % exc)
        return 1
    present = dict(listed())
    for name, (sha, size) in sorted(wanted.items()):
        path = present.get(name)
        if path is None:
            problems.append('Gone: ' + name)
        elif path.stat().st_size != size or digest(path) != sha:
            problems.append('been altered: ' + name)
    problems += ['extra:' + name for name in sorted(set(present) - set(wanted))]
    for item in sorted(VENDOR.rglob('*')):
        if item.suffix == '.pyd' or item.name in ('main.py', '__pycache__'):
            problems.append('should not be here:' + item.relative_to(ROOT / 'pc').as_posix())
    print('List %d files; modified, missing or extra:%s' % (len(wanted), 'None' if not problems else ''))
    for problem in problems:
        print('  ' + problem)
    return 1 if problems else 0


def compare_with_v7(folder):
    folder = Path(folder)
    if not folder.is_dir():
        print('not found V7 folder: %s' % folder)
        return 1
    different = 0
    print('and %s comparison:' % folder)
    for part in V7_PARTS:
        for item in sorted((VENDOR / 'v7' / part).rglob('*')):
            if not item.is_file():
                continue
            relative = item.relative_to(VENDOR / 'v7')
            source = folder / relative
            if not source.is_file():
                verdict = 'V7 does not have this file'
            elif source.read_bytes() == item.read_bytes():
                verdict = 'same'
            else:
                verdict, different = 'different', different + 1
            print('  %-6s %s' % (verdict, relative.as_posix()))
    for part in ('backend', 'backend/functions'):
        ours = {item.name for item in (VENDOR / 'v7' / part).glob('*.py')}
        extra = sorted(item.name for item in (folder / part).glob('*.py') if item.name not in ours)
        if extra:
            print('  V7 of %s but not copied here: Pump steps in%s' % (part, ', '.join(extra)))
    site = folder / '.venv' / 'Lib' / 'site-packages'
    for package in ('serial', 'yaml'):
        if not (site / package).is_dir():
            print('  V7 of .venv Not in %s,skip' % package)
            continue
        changed = [item.relative_to(VENDOR / 'site').as_posix() for item in sorted((VENDOR / 'site' / package).rglob('*.py'))
                   if not (site / item.relative_to(VENDOR / 'site')).is_file()
                   or (site / item.relative_to(VENDOR / 'site')).read_bytes() != item.read_bytes()]
        different += len(changed)
        print('  %s: %s' % (package, 'and V7 of .venv same' if not changed else 'different: ' + ', '.join(changed)))
    calibration = ROOT / 'pc' / 'data' / 'pumpboard' / 'scale_calibration.json'
    source = folder / 'config' / 'scale_calibration.json'
    if calibration.is_file() and source.is_file():
        same = calibration.read_bytes() == source.read_bytes()
        print('   (reference) Calibration file of the scale:pc\\data\\pumpboard that share and V7 of%s' % ('same' if same else 'is different (it will be different after each correction, this is not a mistake)'))
    return 1 if different else 0


def main(argv):
    if '--write-manifest' in argv:
        return write_manifest()
    code = check_manifest()
    if '--v7' in argv:
        index = argv.index('--v7')
        folder = argv[index + 1] if len(argv) > index + 1 else Path(os.environ.get('USERPROFILE', '')) / 'Desktop' / 'independent laboratoryv7'
        code = max(code, compare_with_v7(folder))
    return code


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')  # a console in another code page shows "?" instead of failing
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main(sys.argv[1:]))
