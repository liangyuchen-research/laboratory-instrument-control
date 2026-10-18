#!/usr/bin/env python3
"""Plasmic Electronic control TCP Client: one per line JSON,One question and one answer.Only use Python standard function library.

When used as a function library (your own program import) : 

    from plasmic_client import Client
    with Client('192.168.1.1', token='Paste RPi of token') as plasmic:
        print(plasmic.request('status')['snapshot']['power'])

When used as a command line tool:

    python plasmic_client.py --host 192.168.1.1 --token XXXX status
    python plasmic_client.py --host 127.0.0.1 ping
    python plasmic_client.py --host 192.168.1.1 --token XXXX set_parameters parameters={"Ontime":25,...}
    python plasmic_client.py --host 192.168.1.1 --token XXXX --confirm-high-voltage plasma_run

discharge (plasma_run) , the connection under which the command is issued must remain connected and continue to send messages (any command counts,
For example once per second job) ; Interrupted or exceeded the server’s watchdog_s seconds, the server will automatically stop discharging.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time

# 2: plasma_run no longer takes an order to choose from; the RPi measures the one way its settings
#    say (hello["flow"], one background frame). A side that still speaks 1 is told to update.
PROTOCOL = 2
DEFAULT_PORT = 8767
# Seconds to wait for the reply. Commands that talk to the equipment take a while;
# plasma_run and spectrum answer at once with a job id and are then followed with "job".
REPLY_SECONDS = {'stop': 120, 'prepare': 60, 'clear_fault': 60, 'set_parameters': 60,
                 'status': 40, 'spectrum': 40, 'plasma_run': 40, 'result': 60}


class PlasmicError(Exception):
    """The server answered {"ok": false}; ``code`` is the machine-readable reason."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Client:
    def __init__(self, host, port=DEFAULT_PORT, token='', timeout=5.0):
        self.host, self.port, self.token, self.timeout = host, int(port), token, timeout
        self.sock = None
        self.file = None
        self.hello = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()

    def connect(self):
        """Open the connection, check the protocol version and sign in with the token."""
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.file = self.sock.makefile('rb')
        try:
            self.hello = self.request('hello')
            if self.hello.get('protocol') != PROTOCOL:
                raise PlasmicError('protocol', f'server protocol version {self.hello.get("protocol")} with this program {PROTOCOL} is different, please update both ends.')
            if self.hello.get('auth_required'):
                if not self.token:
                    raise PlasmicError('auth_required', 'Server needs token,Please fill in the connection settings')
                self.request('auth', token=self.token)
        except Exception:
            self.close()
            raise
        return self.hello

    def close(self):
        for item in (self.file, self.sock):
            try:
                if item is not None:
                    item.close()
            except OSError:
                pass
        self.sock = self.file = None

    @property
    def connected(self):
        return self.sock is not None

    def raw(self, line, timeout=15.0):
        """Send one line exactly as given and return the reply line (for learning and testing)."""
        if self.sock is None:
            raise ConnectionError('not connected yet')
        try:
            self.sock.settimeout(timeout)
            try:
                self.sock.sendall(line.strip().encode('utf-8') + b'\n')
            except OSError:
                pass  # the server may already have said why it is closing; read that below
            reply = self.file.readline()
        except OSError as exc:
            self.close()
            raise ConnectionError(f'The connection to the server is interrupted:{exc}') from exc
        if not reply:
            self.close()
            raise ConnectionError('The server closed the connection')
        return reply.decode('utf-8').rstrip('\n')

    def request(self, cmd, timeout=None, **fields):
        """Send {"cmd": cmd, ...fields}; return the reply dict or raise PlasmicError."""
        message = {'cmd': cmd}
        message.update(fields)
        reply = json.loads(self.raw(json.dumps(message, ensure_ascii=False, allow_nan=False),
                                    timeout or REPLY_SECONDS.get(cmd, 15)))
        if reply.get('ok') is not True:
            raise PlasmicError(reply.get('code', 'error'), reply.get('error', 'Server report failure'))
        return reply

    def wait_job(self, on_progress=None, poll_seconds=1.0):
        """Poll "job" until the current job ends; the polling also feeds the watchdog."""
        while True:
            job = self.request('job')['job']
            if job is None or job['state'] != 'running':
                return job
            if on_progress:
                on_progress(job)
            time.sleep(poll_seconds)

    def run_plasma(self, on_progress=None):
        """One discharge + spectrum job (HIGH VOLTAGE). Returns the finished job."""
        self.request('plasma_run', confirm_high_voltage=True)
        return self.wait_job(on_progress)


def stop(host, port=DEFAULT_PORT, timeout=5.0):
    """HSPY OFF + Nano STOP over its own short connection; needs no token."""
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.settimeout(REPLY_SECONDS['stop'])
        sock.sendall(b'{"cmd":"stop"}\n')
        with sock.makefile('rb') as stream:
            line = stream.readline()
    if not line:
        raise ConnectionError('The server did not reply stop')
    return json.loads(line)


def _value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def main():
    parser = argparse.ArgumentParser(description='Plasmic Electronic control TCP command line tools')
    parser.add_argument('--host', default='192.168.1.1')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--token', default='')
    parser.add_argument('--confirm-high-voltage', action='store_true', help='plasma_run must be added')
    parser.add_argument('cmd', help='hello, ping, status, prepare, clear_fault, stop, spectrum, plasma_run, job, jobs, result, set_parameters')
    parser.add_argument('fields', nargs='*', help='Other fields, written as name=value, for example What is fresh=true')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:  # replies contain Chinese; never depend on the console's code page
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    fields = {}
    for item in args.fields:
        name, separator, text = item.partition('=')
        if not separator:
            parser.error(f'field must be written as Name=value:{item}')
        fields[name] = _value(text)
    try:
        if args.cmd == 'stop':
            reply = stop(args.host, args.port)
        else:
            with Client(args.host, args.port, args.token) as client:
                if args.cmd == 'plasma_run':
                    if not args.confirm_high_voltage:
                        parser.error('plasma_run will output high voltage and must be added --confirm-high-voltage')
                    reply = {'ok': True, 'job': client.run_plasma(
                        lambda job: print(f'  {job["elapsed_s"]:6.1f} s  {job["progress"]["phase"]}', file=sys.stderr))}
                elif args.cmd == 'spectrum':
                    client.request('spectrum', **fields)
                    reply = {'ok': True, 'job': client.wait_job()}
                else:
                    reply = client.request(args.cmd, **fields)
    except (PlasmicError, OSError) as exc:
        print(json.dumps({'ok': False, 'code': getattr(exc, 'code', 'connection'), 'error': str(exc)},
                         ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    return 0 if reply.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
