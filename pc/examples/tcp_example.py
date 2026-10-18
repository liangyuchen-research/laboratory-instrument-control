#!/usr/bin/env python3
r"""does not require any function library, just use TCP socket and Plasmic Minimal example of electronically controlled speech.

Use the simulator to practice first (execute on the computer Start-Demo.cmd,simulator will be opened in 127.0.0.1:8767,unnecessary token) : 

    ..\runtime\python\python.exe tcp_example.py

Lian Zhen RPi hour,Bundle HOST changed to RPi of IP (For example 192.168.0.82) , TOKEN changed to RPi on token.
"""
import json
import socket
import time

HOST = '127.0.0.1'
PORT = 8767
TOKEN = ''
RUN_PLASMA = False  # changed to True will really discharge (high voltage!)


def ask(stream, message):
    """Send one line JSON,Read one line of reply."""
    stream.write((json.dumps(message) + '\n').encode('utf-8'))
    stream.flush()
    reply = json.loads(stream.readline())
    if not reply['ok']:
        raise RuntimeError(f'{reply["code"]}: {reply["error"]}')
    return reply


with socket.create_connection((HOST, PORT), timeout=5) as sock:
    sock.settimeout(60)
    stream = sock.makefile('rwb')

    hello = ask(stream, {'cmd': 'hello'})
    print('Server version', hello['version'], '｜simulation mode' if hello['sim'] else '')
    if hello['auth_required']:
        ask(stream, {'cmd': 'auth', 'token': TOKEN})

    status = ask(stream, {'cmd': 'status', 'fresh': True})
    power = status['snapshot']['power']
    print('HSPY', 'ON' if power['run_stop_setting'] else 'OFF', 'settings', power['set_voltage_v'], 'V')
    print('Nano', status['snapshot']['nano']['state'], '｜can be discharged:', status['ready']['plasma'])
    for problem in status['ready']['problems']:
        print('  cannot be discharged yet:', problem['reason'], '→', problem['fix'])

    if RUN_PLASMA and status['ready']['plasma']:
        started = ask(stream, {'cmd': 'plasma_run', 'confirm_high_voltage': True})
        print('starts discharging', started['voltage_v'], 'V,Work', started['job'])
        while True:
            # asks about the progress every second. This is also a signal of ”I am still here”: more than watchdog_s The server will automatically stop discharging within seconds.
            job = ask(stream, {'cmd': 'job'})['job']
            if job['state'] != 'running':
                break
            print(' ', job['progress']['phase'], job['elapsed_s'], 'Second')
            time.sleep(1)
        print('Result:', job['state'], job['error'])
        if job['state'] == 'done':
            result = ask(stream, {'cmd': 'result'})
            top = max(range(result['pixels']), key=result['difference'].__getitem__)
            print('highest peak', result['axis'][top], 'nm,strength', result['difference'][top])
