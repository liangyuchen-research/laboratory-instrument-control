#!/usr/bin/python3
"""Set UNO220 trigger pin 3 LOW without ever requesting HIGH.

This only establishes a software default after Linux starts. It does not
verify Nano D13, MOSFET state, or power-on/reset transients.
"""
from pathlib import Path
import time


def main():
    root = Path('/sys/class/gpio')
    deadline = time.monotonic() + 15
    while True:
        chips = [p.parent for p in root.glob('gpiochip*/label')
                 if p.read_text().strip() == '1-0027']
        if len(chips) == 1:
            break
        if len(chips) > 1 or time.monotonic() >= deadline:
            raise RuntimeError('Expected exactly one UNO220 I2C GPIO chip 1-0027')
        time.sleep(0.1)
    chip = chips[0]
    if int((chip / 'ngpio').read_text()) != 8:
        raise RuntimeError('Unexpected GPIO chip width')
    number = int((chip / 'base').read_text()) + 3
    pin = root / ('gpio' + str(number))
    if not pin.exists():
        (root / 'export').write_text(str(number))
    while not (pin / 'direction').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('GPIO export did not become ready')
        time.sleep(0.01)
    if (pin / 'active_low').read_text().strip() != '0':
        raise RuntimeError('Unexpected active_low mapping; refusing to invert it')
    # Kernel sysfs API sets direction and initial LOW together.
    (pin / 'direction').write_text('low\n')
    if ((pin / 'direction').read_text().strip() != 'out'
            or (pin / 'value').read_text().strip() != '0'):
        raise RuntimeError('Trigger LOW readback failed')
    print('UNO220 pin 3 configured as output LOW; GPIO %d' % number)


if __name__ == '__main__':
    main()
