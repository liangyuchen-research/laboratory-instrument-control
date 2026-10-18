"""compilation pump control board (Arduino Mega 2560) generates mega\\hex\\ Two of them .hex.

    .\\pc\\runtime\\python\\python.exe -B .\\mega\\tools\\build_firmware.py

requires that this computer has been installed Arduino IDE 2.x (Use the included What is arduino-cli) , “Arduino AVR Boards”and
AccelStepper library. Nothing will be downloaded, no ports will be touched, no burning will occur.

  mega\\hex\\plasmic-mega.hex   mega\\source\\PlasmicMega (run first make_firmware.py generated)
  mega\\hex\\v7-stock.hex       pc\\vendor\\v7\\firmware\\Arduino (independent laboratory v7 Original firmware, return for use)

2026-10-07 On this computer:v7-stock.hex and the content read from the board at that time (mega\\backup\\) bit-by-bit identical,
So it was compiled using the same compiler plasmic-mega.hex,and the original firmware on the board is the modification of the source code. After
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEX = ROOT / 'mega' / 'hex'
SKETCHES = (('plasmic-mega.hex', ROOT / 'mega' / 'source' / 'PlasmicMega', 'PlasmicMega.ino'),
            ('v7-stock.hex', ROOT / 'pc' / 'vendor' / 'v7' / 'firmware' / 'Arduino', 'Arduino.ino'))
FQBN = 'arduino:avr:mega'


def find_cli():
    candidates = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Arduino IDE' / 'resources' / 'app' / 'lib'
                  / 'backend' / 'resources' / 'arduino-cli.exe',
                  Path(os.environ.get('ProgramFiles', '')) / 'Arduino IDE' / 'resources' / 'app' / 'lib' / 'backend'
                  / 'resources' / 'arduino-cli.exe']
    found = shutil.which('arduino-cli')
    if found:
        candidates.insert(0, Path(found))
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit('not found arduino-cli: Please install first Arduino IDE 2.x (has a compiler inside).')


def main():
    cli = find_cli()
    config = Path.home() / '.arduinoIDE' / 'arduino-cli.yaml'
    base = [str(cli)] + (['--config-file', str(config)] if config.is_file() else [])
    HEX.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='plasmic-mega-') as work:
        for name, folder, sketch in SKETCHES:
            if not (folder / sketch).is_file():
                raise SystemExit('None %s (Execute first make_firmware.py) ' % (folder / sketch))
            # The sketch is compiled from a copy: the compiler writes nothing beside the sources.
            copy = Path(work) / name / Path(sketch).stem
            copy.mkdir(parents=True)
            for item in folder.iterdir():
                if item.suffix in ('.ino', '.h'):
                    shutil.copyfile(item, copy / item.name)
            out = Path(work) / name / 'out'
            done = subprocess.run(base + ['compile', '--warnings', 'all', '--fqbn', FQBN, '--output-dir', str(out), str(copy)],
                                  capture_output=True)
            text = (done.stdout + done.stderr).decode('utf-8', 'replace')
            if done.returncode != 0:
                sys.stdout.write(text)
                raise SystemExit('%s compilation failed' % sketch)
            if 'warning' in text.lower():
                sys.stdout.write(text)
            built = out / (sketch + '.hex')
            shutil.copyfile(built, HEX / name)
            data = (HEX / name).read_bytes()
            print('%-18s %6d byte hex files  SHA-256 %s' % (name, len(data), hashlib.sha256(data).hexdigest()))
            for line in text.splitlines():
                if 'byte' in line or 'bytes' in line:
                    print('   ', line.strip())


if __name__ == '__main__':
    main()
