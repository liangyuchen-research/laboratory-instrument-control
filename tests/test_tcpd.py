"""RPi TCP Automatic test of server (simulated device, no hardware required).

    python tests/test_tcpd.py
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'rpi' / 'tcpd'), str(ROOT / 'pc'), str(ROOT / 'rpi' / 'rpi_tools'), str(ROOT / 'tests')]

import plasma_measure  # noqa: E402  (only its parameter rule and signal handler are used here: nothing is run)
import plasmic_tcpd as tcpd  # noqa: E402
from plasmic_client import Client, PlasmicError, stop as client_stop  # noqa: E402
from nano_verify import encode_parameters  # noqa: E402  (the field-verified encoder)

GOOD = {'Ontime': 25.0, 'Offtime': 5, 'Cycle': 25, 'Loopnum': 8,
        'Integration_Time': 1000, 'Spectra_Interval': 1500, 'Voltage': 100}


class SimServer:
    """A sim server on a free local port, torn down after each test."""

    def __init__(self, speed=40, discovery=False, **overrides):
        self.temp = tempfile.TemporaryDirectory()
        config = tcpd.load_config(None, dict({'bind': '127.0.0.1', 'port': 8767, 'allow': ['127.0.0.1'],
                                              'output_dir': str(Path(self.temp.name) / 'captures')}, **overrides))
        self.backend = tcpd.SimBackend(self.temp.name, speed)
        self.controller = tcpd.Controller(config, self.backend)
        for _attempt in range(30):
            self.server = tcpd.Server(('127.0.0.1', 0), tcpd.Handler)
            self.port = config['port'] = self.server.server_address[1]
            if not discovery:
                break
            try:  # answers on UDP 127.0.0.1, same port number; never heard outside this computer
                finder = tcpd.Discovery(self.controller)
            except OSError:  # some other program already uses that number for UDP: take another one
                self.server.server_close()
                continue
            finder.start()
            break
        else:
            raise OSError('no port number is free for both TCP and UDP')
        self.server.controller = self.controller
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def ask_udp(self, data, timeout=2.0):
        """One datagram to the discovery port; the decoded reply, or None when nothing came back."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(data, ('127.0.0.1', self.port))
            try:
                return json.loads(sock.recvfrom(2048)[0].decode('utf-8'))
            except (socket.timeout, ConnectionError):
                return None

    def client(self, token=''):
        return Client('127.0.0.1', self.port, token).__enter__()

    def close(self):
        self.controller.begin_shutdown()
        self.server.shutdown()
        self.controller.wait_for_job(10)
        self.server.server_close()
        self.temp.cleanup()


def wait_until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('condition not reached in time')


class Base(unittest.TestCase):
    speed = 40
    overrides = {}

    def setUp(self):
        self.sim = SimServer(self.speed, **self.overrides)
        self.addCleanup(self.sim.close)
        self.clients = []

    def client(self, token=''):
        client = self.sim.client(token)
        self.addCleanup(client.close)
        return client

    def first_reply_read_late(self):
        """What a newcomer is told when its own first line is late and it reads later still.

        A server that has hung up by then answers that line with a reset, and Windows drops
        the reply it was holding before the program has read it.
        """
        with socket.create_connection(('127.0.0.1', self.sim.port), timeout=5) as sock:
            time.sleep(.2)  # the server has answered by now and would like to hang up
            sock.sendall(b'{"cmd":"hello"}\n')
            time.sleep(.2)
            with sock.makefile('rb') as stream:
                return json.loads(stream.readline())


class WatchdogTests(Base):
    """The council's first requirement: losing the controlling link stops the discharge."""
    speed = 2
    overrides = {'watchdog_s': 2}

    def start_long_job(self, client):
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=40))
        job = client.request('plasma_run', confirm_high_voltage=True)

        def supply_on():  # keep talking, as a real owner must, until the simulated supply is ON
            client.request('ping')
            return self.sim.backend.run_stop == 1
        wait_until(supply_on)
        return job['job']

    def finished(self):
        job = self.sim.controller.job
        return job if job.state != 'running' else None

    def test_owner_disconnect_turns_high_voltage_off(self):
        owner = self.client()
        job_id = self.start_long_job(owner)
        owner.close()  # cable pulled / program crashed
        job = wait_until(self.finished)
        self.assertEqual((job.id, job.state, job.cancel_reason), (job_id, 'cancelled', 'owner_disconnected'))
        self.assertEqual((self.sim.backend.run_stop, self.sim.backend.voltage), (0, 0.0))
        self.assertEqual(job.post_stop, {'ok': True, 'error': ''})
        self.assertLess(job.finished_mono - job.started_mono, 8)

    def test_silent_owner_times_out(self):
        owner = self.client()
        self.start_long_job(owner)
        silent_since = time.monotonic()
        job = wait_until(self.finished)  # the owner stays connected but sends nothing
        self.assertEqual((job.state, job.cancel_reason), ('cancelled', 'watchdog_timeout'))
        self.assertGreaterEqual(time.monotonic() - silent_since, 1.9)
        self.assertEqual(self.sim.backend.run_stop, 0)
        self.assertTrue(owner.connected)

    def test_polling_owner_keeps_job_alive_and_other_clients_do_not_count(self):
        owner, other = self.client(), self.client()
        self.start_long_job(owner)
        end = time.monotonic() + 3.2
        while time.monotonic() < end:  # longer than the 2 s watchdog
            self.assertEqual(owner.request('job')['job']['state'], 'running')
            time.sleep(.4)
        owner_quiet = time.monotonic()
        while self.sim.controller.job.state == 'running':  # now only the other client talks
            other.request('ping')
            time.sleep(.2)
            self.assertLess(time.monotonic() - owner_quiet, 8)
        self.assertEqual(self.sim.controller.job.cancel_reason, 'watchdog_timeout')

    def test_stop_from_another_connection_without_token(self):
        owner = self.client()
        job_id = self.start_long_job(owner)
        reply = client_stop('127.0.0.1', self.sim.port)
        self.assertEqual((reply['ok'], reply['cancel_sent'], reply['job']), (True, True, job_id))
        job = wait_until(lambda: owner.request('job')['job'] if self.finished() else None)
        self.assertEqual((job['state'], job['cancel_reason']), ('cancelled', 'stop'))
        self.assertEqual(self.sim.backend.run_stop, 0)

    def test_a_job_is_asked_to_stop_only_once(self):
        owner = self.client()
        self.start_long_job(owner)
        runner = self.sim.controller.job.runner
        self.assertTrue(runner.terminate())
        self.assertFalse(runner.terminate())
        owner.close()  # watchdog would also like to stop it: must not signal again
        wait_until(self.finished)
        self.assertEqual(self.sim.controller.job.cancel_reason, '')  # stopped by the first, direct request


class ProtocolTests(Base):
    def test_hello_ping_and_bare_words(self):
        client = self.client()
        self.assertEqual((client.hello['server'], client.hello['protocol'], client.hello['sim']),
                         ('plasmic-tcpd', 2, True))
        self.assertEqual(tcpd.PROTOCOL, 2)  # 1 let the sender choose an order for plasma_run
        self.assertEqual(client.hello['limits']['max_voltage_v'], 700)
        self.assertTrue(json.loads(client.raw('ping'))['ok'])
        self.assertEqual(json.loads(client.raw('{"cmd":"ping","id":7}'))['id'], 7)
        for line, code in (('{bad', 'bad_request'), ('[1]', 'unknown_command'), ('{"cmd":1}', 'bad_request'),
                           ('{"cmd":"launch"}', 'unknown_command'), ('{"x":1}', 'bad_request')):
            self.assertEqual(json.loads(client.raw(line))['code'], code, line)
        self.assertTrue(json.loads(client.raw('status'))['ok'])  # the connection survived the bad lines

    def test_one_connection_too_many_is_told_so(self):
        first = self.client()
        for _ in range(tcpd.MAX_CONNECTIONS - 1):
            self.client()
        self.assertEqual(self.first_reply_read_late()['code'], 'busy')
        first.close()
        wait_until(lambda: self.sim.controller.connections < tcpd.MAX_CONNECTIONS)
        self.assertTrue(self.client().request('ping')['ok'])  # the one turned away took no place

    def test_status_shape_and_readiness(self):
        status = self.client().request('status', fresh=True)
        self.assertEqual(status['snapshot']['power']['run_stop_setting'], 0)
        self.assertEqual(status['snapshot']['nano']['firmware'], '1.2.1')
        self.assertEqual(status['snapshot']['parameter']['voltage_v'], 100)
        self.assertEqual(status['ready'], {'plasma': True, 'problems': []})
        self.assertFalse(status['busy'])
        self.assertIsInstance(status['estimate_s'], int)

    def test_parameters_are_validated_and_saved(self):
        client = self.client()
        for change, text in (({'Voltage': 701}, '1–700'), ({'Voltage': 100.5}, 'integer'), ({'Ontime': .05}, 'Ontime'),
                             ({'Cycle': 5000, 'Loopnum': 3}, '10000'), ({'Integration_Time': 2000}, 'Spectra_Interval'),
                             ({'Spectra_Interval': 20000}, '100 Second'), ({'Ontime': True}, 'number')):
            with self.assertRaises(PlasmicError) as caught:
                client.request('set_parameters', parameters=dict(GOOD, **change))
            self.assertEqual(caught.exception.code, 'bad_request')
            self.assertIn(text, str(caught.exception))
        with self.assertRaises(PlasmicError):
            client.request('set_parameters', parameters=dict(GOOD, Extra=1))
        saved = client.request('set_parameters', parameters=dict(GOOD, Voltage=250))
        self.assertTrue(saved['nano_sync_required'])
        status = client.request('status')
        self.assertEqual((status['snapshot']['parameter']['voltage_v'], status['snapshot']['power']['set_voltage_v']),
                         (250, 250))

    def test_the_pretend_sample_decides_the_metal_lines(self):
        """Simulator only (practice mode of the PC program): what the pretend sample holds sets the
        height of its Cu / Zn / Ni lines; the plasma's own lines do not depend on it."""
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Voltage=400, Loopnum=4))

        def heights():
            client.request('plasma_run', confirm_high_voltage=True)
            client.wait_job(poll_seconds=.02)
            result = client.request('result')
            near = lambda nm: max(value for x, value in zip(result['axis'], result['difference']) if abs(x - nm) < .3)  # noqa: E731
            return {name: near(nm) for name, nm in (('Zn', 213.43), ('Ni', 231.78), ('Cu', 324.83), ('N2', 337.13),
                                                    ('shift1', 278.8), ('shift2', 331.8))}
        usual = heights()                                        # 0.5 mg/L of each
        self.assertEqual(max(usual, key=usual.get), 'N2')        # the strongest line is still the plasma's own
        self.assertEqual(client.request('sim_sample', sample={'Cu': 2.0})['sample'], {'Cu': 2.0, 'Zn': 0.0, 'Ni': 0.0})
        strong = heights()
        self.assertAlmostEqual(strong['Cu'] / usual['Cu'], 4.0, delta=.6)
        self.assertLess(strong['Zn'], usual['Zn'] * .15)         # no zinc, no nickel: only noise is left there
        self.assertLess(strong['Ni'], usual['Ni'] * .2)
        for name in ('N2', 'shift1', 'shift2'):
            self.assertAlmostEqual(strong[name] / usual[name], 1.0, delta=.15)
        self.assertEqual(client.request('sim_sample', sample={'Cu': 0, 'Zn': 0, 'Ni': 0})['sample'],
                         {'Cu': 0.0, 'Zn': 0.0, 'Ni': 0.0})      # a blank
        blank = heights()
        self.assertLess(blank['Cu'], usual['Cu'] * .1)
        self.assertGreater(blank['shift1'], usual['shift1'] * .8)
        self.assertEqual(client.request('sim_sample', sample=None)['sample'], {'Cu': .5, 'Zn': .5, 'Ni': .5})
        for bad in ({'Pb': 1}, {'Cu': -1}, {'Cu': 'much'}, {'Cu': True}, [1, 2, 3]):
            with self.assertRaises(PlasmicError) as caught:
                client.request('sim_sample', sample=bad)
            self.assertEqual(caught.exception.code, 'bad_request')

    def test_fault_blocks_discharge_until_cleared(self):
        client = self.client()
        client.request('sim_fault', fault='HEARTBEAT_LOST')
        status = client.request('status')
        self.assertFalse(status['ready']['plasma'])
        self.assertIn('HEARTBEAT_LOST', status['ready']['problems'][0]['reason'])
        with self.assertRaises(PlasmicError) as caught:
            client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual(caught.exception.code, 'not_ready')
        with self.assertRaises(PlasmicError) as caught:
            client.request('prepare')
        self.assertEqual(caught.exception.code, 'tool_failed')
        cleared = client.request('clear_fault')
        self.assertEqual((cleared['cleared'], cleared['previous_fault']), (True, 'HEARTBEAT_LOST'))
        self.assertTrue(client.request('prepare')['prepared'])
        self.assertTrue(client.request('status')['ready']['plasma'])

    def test_plasma_needs_explicit_confirmation(self):
        client = self.client()
        for fields in ({}, {'confirm_high_voltage': 'yes'}, {'confirm_high_voltage': 1}):
            with self.assertRaises(PlasmicError) as caught:
                client.request('plasma_run', **fields)
            self.assertEqual(caught.exception.code, 'bad_request')
        self.assertIsNone(self.sim.controller.job)

    def test_full_plasma_run_and_result(self):
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Voltage=400, Loopnum=5))
        started = client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual((started['voltage_v'], started['watchdog_s']), (400, 5))
        with self.assertRaises(PlasmicError) as caught:  # one job at a time
            self.client().request('prepare')
        self.assertEqual(caught.exception.code, 'busy')
        phases = []
        job = client.wait_job(lambda view: phases.append(view['progress']['phase']), poll_seconds=.05)
        self.assertEqual((job['state'], job['error'], job['post_stop'], job['high_voltage']), ('done', '', None, True))
        self.assertIn('signal', phases)
        self.assertIn('Nano DONE, Spectrum completed', job['steps'])
        result = client.request('result', frames=True)
        self.assertEqual((result['job'], result['kind'], result['signal_frames'], result['background_frames']),
                         (started['job'], 'plasma', 5, 1))
        self.assertEqual({len(result[key]) for key in ('axis', 'background', 'signal', 'difference')}, {1880})
        self.assertEqual((len(result['frames']), len(result['frames'][0])), (5, 1880))
        peak = result['axis'][max(range(1880), key=lambda i: result['difference'][i])]
        self.assertAlmostEqual(peak, 337.13, delta=.3)  # the strongest simulated line
        self.assertEqual(result['parameters']['voltage_v'], 400)
        self.assertNotIn('frames', client.request('result'))
        listed = client.request('jobs')['jobs']
        self.assertEqual((listed[0]['job'], listed[0]['valid'], listed[0]['frames']), (started['job'], True, 5))
        status = client.request('status')
        self.assertEqual((status['snapshot']['power']['run_stop_setting'], status['busy']), (0, False))

    def test_capture_job(self):
        client = self.client()
        started = client.request('spectrum', integration_ms=100)
        self.assertTrue(started['job'].startswith('CAP-'))
        job = client.wait_job(poll_seconds=.05)
        self.assertEqual((job['state'], job['high_voltage']), ('done', False))
        result = client.request('result', job=started['job'])
        self.assertEqual((result['kind'], len(result['frames']), len(result['mean'])), ('capture', 3, 1880))

    def test_result_rejects_paths(self):
        client = self.client()
        for name in ('../captures', 'HMI-xyz', '', 5, 'HMI-' + '0' * 32):
            with self.assertRaises(PlasmicError) as caught:
                client.request('result', job=name)
            self.assertIn(caught.exception.code, ('bad_request', 'not_found'))

    def test_idle_stop_reads_back_off(self):
        reply = client_stop('127.0.0.1', self.sim.port)
        self.assertEqual((reply['ok'], reply['cancel_sent'], reply['stopped']), (True, False, True))
        self.assertEqual(reply['power']['run_stop_setting'], 0)


class AccessTests(Base):
    overrides = {'token': 'secret-token-for-tests'}

    def test_token_is_required_except_for_safe_commands(self):
        with socket.create_connection(('127.0.0.1', self.sim.port), timeout=5) as sock:
            stream = sock.makefile('rwb')

            def ask(line):
                stream.write(line.encode() + b'\n')
                stream.flush()
                return json.loads(stream.readline())
            self.assertTrue(ask('hello')['auth_required'])
            self.assertTrue(ask('ping')['ok'])
            for command in ('status', 'prepare', 'jobs', 'result', 'spectrum', 'clear_fault'):
                self.assertEqual(ask(json.dumps({'cmd': command}))['code'], 'auth_required', command)
            self.assertEqual(ask('{"cmd":"plasma_run","confirm_high_voltage":true}')['code'], 'auth_required')
            self.assertEqual(ask('{"cmd":"auth","token":"wrong"}')['code'], 'auth_failed')
            self.assertEqual(ask('{"cmd":"status","token":"wrong"}')['code'], 'auth_failed')
            self.assertEqual(ask('{"cmd":"status"}')['code'], 'auth_required')
            self.assertTrue(ask('stop')['ok'])  # stopping never needs a token
            self.assertTrue(ask('{"cmd":"status","token":"secret-token-for-tests"}')['ok'])
            self.assertTrue(ask('{"cmd":"status"}')['ok'])  # the connection stays signed in
        self.assertIsNone(self.sim.controller.job)

    def test_client_signs_in(self):
        self.assertTrue(self.client('secret-token-for-tests').request('status')['ok'])
        with self.assertRaises(PlasmicError) as caught:
            self.client('nope')
        self.assertEqual(caught.exception.code, 'auth_failed')
        with self.assertRaises(PlasmicError) as caught:
            self.client('')
        self.assertEqual(caught.exception.code, 'auth_required')


class AllowListTests(Base):
    overrides = {'allow': ['192.168.1.2']}

    def test_other_addresses_are_refused_with_a_reason(self):
        with self.assertRaises(PlasmicError) as caught:
            self.client()
        self.assertEqual(caught.exception.code, 'forbidden')
        self.assertIn('127.0.0.1', str(caught.exception))
        with self.assertRaises((ConnectionError, OSError, ValueError)):  # even stop: the address is not trusted
            reply = client_stop('127.0.0.1', self.sim.port)
            if reply.get('code') == 'forbidden':
                raise ConnectionError('forbidden')

    def test_the_reason_still_arrives_when_the_other_side_reads_late(self):
        self.assertEqual(self.first_reply_read_late()['code'], 'forbidden')

    def test_a_whole_network_can_be_allowed(self):
        """For an RPi on a router, where the computer's address may change: "192.168.0.*"."""
        self.assertTrue(tcpd.allowed('192.168.0.130', ['127.0.0.1', '192.168.0.*']))
        self.assertFalse(tcpd.allowed('192.168.10.130', ['192.168.0.*']))
        self.assertFalse(tcpd.allowed('192.168.0.130', ['192.168.0.13']))
        self.assertTrue(tcpd.allowed('10.1.2.3', ['*']))
        self.assertFalse(tcpd.allowed('10.1.2.3', ['127.0.0.1']))


class DiscoveryTests(unittest.TestCase):
    """“automatic search RPi”: one UDP question, one answer that says who the server is and nothing else."""

    def serve(self, **overrides):
        sim = SimServer(discovery=True, **overrides)
        self.addCleanup(sim.close)
        return sim

    def test_the_question_is_answered_with_the_identity_only(self):
        sim = self.serve(token='secret-token-for-tests')
        reply = sim.ask_udp(tcpd.DISCOVER_REQUEST)
        self.assertEqual({key: reply[key] for key in ('server', 'version', 'protocol', 'port', 'sim', 'allowed')},
                         {'server': 'plasmic-tcpd', 'version': tcpd.VERSION, 'protocol': tcpd.PROTOCOL,
                          'port': sim.port, 'sim': True, 'allowed': True})
        self.assertTrue(reply['name'])
        self.assertEqual(set(reply), {'server', 'version', 'protocol', 'port', 'sim', 'allowed', 'name'})
        self.assertNotIn('secret-token-for-tests', json.dumps(reply))
        self.assertEqual(sim.ask_udp(tcpd.DISCOVER_REQUEST + b'\r\n')['server'], 'plasmic-tcpd')
        self.assertIsNone(sim.controller.job)  # being found never starts anything

    def test_anything_else_gets_no_answer(self):
        sim = self.serve()
        for data in (b'{"cmd":"stop"}', b'PLASMIC-DISCOVER 2', b'hello', b''):
            self.assertIsNone(sim.ask_udp(data, timeout=.4), data)

    def test_a_computer_that_is_not_on_the_allow_list_is_told_so(self):
        self.assertIs(self.serve(allow=['192.168.1.2']).ask_udp(tcpd.DISCOVER_REQUEST)['allowed'], False)

    def test_answers_are_limited_so_they_cannot_be_used_to_flood(self):
        sim = self.serve()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _ in range(80):
                sock.sendto(tcpd.DISCOVER_REQUEST, ('127.0.0.1', sim.port))
            sock.settimeout(.6)
            answers = 0
            try:
                while True:
                    sock.recvfrom(2048)
                    answers += 1
            except (socket.timeout, ConnectionError):
                pass
        self.assertTrue(1 <= answers <= 40, answers)

    def test_hello_also_says_the_name_and_port(self):
        sim = self.serve()
        client = sim.client()
        self.addCleanup(client.close)
        self.assertEqual((client.hello['port'], client.hello['version']), (sim.port, tcpd.VERSION))
        self.assertTrue(client.hello['name'])

    def test_settings_file_switch(self):
        self.assertIs(tcpd.load_config(None)['discovery'], True)
        with self.assertRaises(ValueError):
            tcpd.load_config(None, {'discovery': 'yes'})
        example = json.loads((ROOT / 'rpi' / 'tcpd' / 'config.example.json').read_text(encoding='utf-8'))
        self.assertEqual(set(example), set(tcpd.DEFAULTS))
        self.assertEqual({key: example[key] for key in ('port', 'watchdog_s', 'poll_s', 'discovery')},
                         {key: tcpd.DEFAULTS[key] for key in ('port', 'watchdog_s', 'poll_s', 'discovery')})


class LcdPowerLineTests(Base):
    """The Nano's LCD shows "POWER ?" 5 s after the last report; every status read is a report."""

    def test_reads_come_often_enough_while_somebody_is_watching(self):
        self.assertLess(self.sim.controller.config['poll_s'], 5)
        times = []
        original = self.sim.backend.status

        def counted():
            times.append(time.monotonic())
            return original()
        self.sim.backend.status = counted
        client = self.client()
        end = time.monotonic() + 8.5
        while time.monotonic() < end:  # what the screen does: ask about once a second
            client.request('status')
            time.sleep(.3)
        gaps = [later - earlier for earlier, later in zip(times, times[1:])]
        self.assertGreaterEqual(len(times), 3, times)
        self.assertLess(max(gaps), 4.5, gaps)   # always inside the LCD's 5 s
        self.assertGreater(min(gaps), 1.0, gaps)  # and the bus is not hammered

    def test_nobody_watching_means_no_reads(self):
        client = self.client()
        client.request('status')
        client.close()
        time.sleep(.5)
        times = []
        original = self.sim.backend.status
        self.sim.backend.status = lambda: times.append(1) or original()
        self.sim.controller.last_activity = float('-inf')  # as if the last message was long ago
        time.sleep(1.5)
        self.assertEqual(times, [])

    def test_simulated_nano_reports_what_its_lcd_shows(self):
        client = self.client()
        self.assertNotIn('lcd_power_report', client.request('status', fresh=True)['snapshot']['nano'])
        client.request('prepare')
        nano = client.request('status', fresh=True)['snapshot']['nano']
        self.assertEqual(nano['lcd_power_report']['power_snapshot'], 'OFF')


class ParameterRuleTests(unittest.TestCase):
    def test_never_looser_than_the_verified_encoder(self):
        """Whatever this server accepts, the field-verified Nano encoder must accept too."""
        values = {'Ontime': (.05, .1, 1, 25.0, 25.05, 999.9, 1000, 1000.1), 'Offtime': (.1, 5, 1000, 1001),
                  'Cycle': (0, 1, 25, 10000, 10001), 'Loopnum': (0, 1, 16, 400, 1000, 1001),
                  'Integration_Time': (0, .5, 1, 100, 1000, 10000, 10001),
                  'Spectra_Interval': (1, 100, 1500, 1500.5, 60000, 60001), 'Voltage': (0, 1, 100, 700, 701)}
        accepted = 0
        for name, options in values.items():
            for option in options:
                for other, more in values.items():
                    for extra in more:
                        candidate = dict(GOOD, **{name: option, other: extra})
                        try:
                            tcpd.check_parameters(candidate)
                        except ValueError:
                            continue
                        accepted += 1
                        encode_parameters(candidate)  # raises if the verified tool would refuse
                        self.assertTrue(type(candidate['Voltage']) is int and 1 <= candidate['Voltage'] <= 700)
                        self.assertTrue(1 <= int(candidate['Integration_Time']) <= 10000)
                        pulse = (candidate['Ontime'] + candidate['Offtime']) * candidate['Cycle']
                        self.assertLessEqual(candidate['Loopnum'] * max(candidate['Spectra_Interval'], pulse), 100000)
        self.assertGreater(accepted, 100)

    def test_what_cannot_work_is_refused_with_the_reason(self):
        """Saved parameters must be ones the measuring program can carry out: the same three checks
        as plasma_measure.pace(), made here so that they cannot even be saved."""
        def refusal(**change):
            try:
                tcpd.check_parameters(dict(GOOD, **change))
            except ValueError as exc:
                return str(exc)
            return ''
        self.assertEqual(refusal(), '')
        # the Nano waits 30 s for the next trigger: no more than 28 s from one loop to the next
        self.assertEqual(refusal(Loopnum=2, Spectra_Interval=28000), '')
        self.assertIn('cannot exceed between two rounds 28 Second', refusal(Loopnum=2, Spectra_Interval=28001))
        self.assertIn('Currently every round 31.00 Second', refusal(Loopnum=2, Spectra_Interval=31000))
        self.assertEqual(refusal(Loopnum=1, Spectra_Interval=60000), '')          # one loop waits for nothing
        self.assertIn('between rounds', refusal(Loopnum=2, Ontime=500, Offtime=500, Cycle=28, Spectra_Interval=1000))  # a 28 s train
        # all loops together: with what one loop really takes, not with the interval that was asked for
        self.assertEqual(refusal(Loopnum=66, Spectra_Interval=1500), '')
        self.assertIn('Number of rounds × Each round time cannot exceed 100 Second', refusal(Loopnum=67, Spectra_Interval=1500))
        self.assertEqual(refusal(Loopnum=111, Spectra_Interval=800, Integration_Time=100), '')   # 0.75 s train + 0.15 s
        self.assertIn('112 wheel × 0.90 Second = 100.8 Second', refusal(Loopnum=112, Spectra_Interval=800, Integration_Time=100))
        short = dict(Ontime=1, Offtime=1, Cycle=5, Integration_Time=20, Spectra_Interval=50)     # never under 0.25 s
        self.assertEqual(refusal(Loopnum=400, **short), '')
        self.assertIn('401 wheel × 0.25 Second', refusal(Loopnum=401, **short))
        self.assertEqual(tcpd.loop_period_us(25000, 5000, 25, 1500000), 1500000)
        self.assertEqual(tcpd.loop_period_us(25000, 5000, 25, 800000), 900000)
        self.assertEqual(tcpd.loop_period_us(1000, 1000, 5, 50000), 250000)

    def test_the_measuring_program_keeps_the_same_rule(self):
        """plasma_measure.pace() and check_parameters() are two copies of one rule: same answer, same
        words, same period, for every case of a table around each of its limits."""
        table = [GOOD, dict(GOOD, Loopnum=5, Voltage=30)]  # the second: what the owner uses
        for loops in (1, 2, 3, 4):
            for interval in (1000, 25000, 27999, 28000, 28001, 30000, 33333, 50000, 60000):
                table.append(dict(GOOD, Loopnum=loops, Spectra_Interval=interval))
        for cycles in (26, 27, 28, 29, 100):          # trains of 26-100 s (1000 ms per pulse)
            for loops in (1, 2, 3):
                table.append(dict(GOOD, Ontime=500, Offtime=500, Cycle=cycles, Loopnum=loops))
        for loops in (60, 66, 67, 100, 110, 111, 112, 125, 133, 134, 399, 400, 401, 999, 1000):
            for interval in (20, 50, 250, 251, 750, 800, 899, 900, 901, 1000, 1500):
                for pulse in ((25.0, 5, 25), (1, 1, 5), (5, 5, 6), (0.1, 0.1, 1)):
                    table.append({'Ontime': pulse[0], 'Offtime': pulse[1], 'Cycle': pulse[2], 'Loopnum': loops,
                                  'Integration_Time': 20, 'Spectra_Interval': interval, 'Voltage': 30})
        compared = refused = 0
        for candidate in table:
            try:
                tcpd.check_parameters(candidate)
                server = ''
            except ValueError as exc:
                server = str(exc)
            if server and not server.startswith(('between rounds', 'Number of rounds × Time per round', 'spectrometer program at most once')):
                continue                              # refused for its range: the other program never gets to see it
            try:
                _train, period = plasma_measure.pace(candidate)
                tool = ''
            except ValueError as exc:
                tool = str(exc)
            self.assertEqual(tool, server, candidate)
            compared += 1
            if server:
                refused += 1
            else:
                on, off = round(candidate['Ontime'] * 1000), round(candidate['Offtime'] * 1000)
                self.assertEqual(round(period * 1e6),
                                 tcpd.loop_period_us(on, off, candidate['Cycle'], candidate['Spectra_Interval'] * 1000), candidate)
        self.assertGreater(compared, 300)
        self.assertGreater(refused, 100)
        self.assertGreater(compared - refused, 100)
        for name in ('LOOP_OVERHEAD_US', 'SHORTEST_PERIOD_US', 'LONGEST_PERIOD_US', 'LONGEST_SIGNAL_US', 'MOST_SIGNAL_FRAMES'):
            self.assertEqual(getattr(tcpd, name), getattr(plasma_measure, name), name)

    def test_readiness_reasons(self):
        good = {'power': {'valid': True, 'run_stop_setting': 0, 'voltage_v': 0, 'current_a': 0,
                          'set_voltage_v': 100, 'set_current_a': .1},
                'nano': {'valid': True, 'firmware': '1.2.1', 'outputs_allowed': True, 'state': 'IDLE',
                         'fault': 'NONE', 'output_command_high': False, 'output_pin_sample': False},
                'parameter': {'valid': True, 'voltage_v': 100, 'integration_ms': 1000, 'on_time_ms': 25.0,
                              'off_time_ms': 5, 'cycles': 25, 'loops': 16, 'spectra_interval_ms': 1500}}
        self.assertEqual(tcpd.ready_problems(good, 1), [])
        self.assertEqual(len(tcpd.ready_problems(good, 21)), 1)
        self.assertEqual(len(tcpd.ready_problems(None, None)), 1)

        def reasons(section, **change):
            snapshot = json.loads(json.dumps(good))
            snapshot[section].update(change)
            return ' '.join(row['reason'] for row in tcpd.ready_problems(snapshot, 1))
        self.assertIn('HSPY power supply is ON', reasons('power', run_stop_setting=1))
        self.assertIn('has not been reset to zero yet', reasons('power', voltage_v=12))
        # No limit on the supply's current-limit setting (the person's decision, 2026-10-07): whatever its panel says.
        for amps in (.5, .999, 3.0):
            self.assertEqual(reasons('power', set_current_a=amps), '', amps)
        self.assertFalse(hasattr(tcpd, 'MAX_CURRENT_A'))
        # The supply's set voltage is not the one of parameter.json: no reason in the unified flow (plasma_measure.py
        # writes it before it switches anything on; the person, 2026-10-07), still one in the verified fall-back.
        for found in (200, 1000.0, 0, 30.5):
            self.assertEqual(reasons('power', set_voltage_v=found), '', found)
        other = json.loads(json.dumps(good))
        other['power']['set_voltage_v'] = 1000.0
        self.assertEqual(tcpd.ready_problems(other, 1, 'unified'), [])
        rows = tcpd.ready_problems(other, 1, 'verified')
        self.assertEqual([row['reason'] for row in rows], ['parameter.json Voltage 100 V and HSPY set value 1000.0 V inconsistent'])
        self.assertEqual(tcpd.ready_problems(good, 1, 'verified'), [])
        self.assertIn('Invalid readback', reasons('power', valid=False, error='timeout'))
        self.assertIn('firmware', reasons('nano', firmware='1.1.0'))
        self.assertIn('fault lock JOB_TIMEOUT', reasons('nano', state='FAULT', fault='JOB_TIMEOUT'))
        self.assertIn('status is RUNNING', reasons('nano', state='RUNNING'))
        self.assertIn('D13', reasons('nano', output_pin_sample=True))
        self.assertIn('1–700', reasons('parameter', voltage_v=0))
        self.assertIn('does not meet the restrictions', reasons('parameter', loops=1000))


STUB_TOOL = r'''
import json, sys, time
action = sys.argv[1]
args = sys.argv[2:]
if action == 'prepare':
    print('noise before the reply')
    print(json.dumps({'valid': True, 'parameters': {'revision': 1}, 'argv': args}))
elif action == 'set_parameters':
    print(json.dumps({'valid': True, 'echo': json.loads(sys.stdin.read()), 'argv': args}))
elif action == 'clear_fault':
    print(json.dumps({'valid': False, 'error': 'Before clearing the fault, you need HSPY OFF and zero output'}))
    sys.exit(2)
elif action == 'stop':
    sys.stderr.write("BlockingIOError: [Errno 11] Resource temporarily unavailable\n")
    sys.exit(1)
elif action == 'plasma_run':
    time.sleep(30)
'''


STUB_MEASURE = r'''
import json, sys
from pathlib import Path
args = sys.argv[1:]
here = Path(__file__).resolve().parent
with open(here / 'calls.log', 'a', encoding='utf-8') as log:
    log.write('supply ' + ' '.join(args) + chr(10))
if (here / 'supply-refuses').exists():
    print(json.dumps({'valid': False, 'error': 'The high voltage power supply does not accept the written set voltage. The set voltage on the 100 V (Readback: Setting 1000.0 V, Run/Stop 0, output 0 V) '}))
    sys.exit(2)
volts = int(args[args.index('--set-voltage') + 1])
print(json.dumps({'valid': True, 'changed': True, 'previous_set_voltage_v': 1000.0, 'set_voltage_v': volts}))
'''

STUB_SAVE = r'''
import json, sys
from pathlib import Path
with open(Path(__file__).resolve().parent / 'calls.log', 'a', encoding='utf-8') as log:
    log.write('file ' + sys.argv[1] + chr(10))
print(json.dumps({'valid': True, 'echo': json.loads(sys.stdin.read())}))
'''


class RealBackendPlumbingTests(unittest.TestCase):
    """The process handling around the real tools, with a stand-in plasma_job.py."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        (Path(self.temp.name) / 'plasma_job.py').write_text(STUB_TOOL, encoding='utf-8')
        self.backend = tcpd.RealBackend({'app_dir': '/app', 'tools_dir': self.temp.name, 'serial_port': '/dev/RS485'})
        # The measuring program lies beside the server (flow_errors is empty); a stand-in is run in its place.
        self.assertEqual(self.backend.flow_errors, {})
        self.backend.measure_tool = Path(self.temp.name) / 'measure_stub.py'
        self.backend.measure_tool.write_text(STUB_MEASURE, encoding='utf-8')

    def calls(self):
        log = Path(self.temp.name) / 'calls.log'
        return log.read_text(encoding='utf-8').splitlines() if log.exists() else []

    def test_arguments_match_what_the_hmi_always_sent(self):
        result = self.backend.prepare()
        self.assertEqual(result['argv'], ['--app-dir', '/app', '--tools-dir', self.temp.name, '--port', '/dev/RS485'])
        result = self.backend.set_parameters(GOOD)
        self.assertEqual(result['echo'], GOOD)
        self.assertEqual(result['argv'][-1], '--execute')

    def test_the_supply_gets_the_voltage_before_the_file_is_saved(self):
        """2026-10-07, 1000.0 V on the supply's panel: the verified tool writes nothing when it finds more than
        700 V or a fraction there, so nothing could be saved. The voltage goes to the supply first, by
        plasma_measure.py --set-voltage (supply OFF, read back); the verified tool then finds it there."""
        (Path(self.temp.name) / 'plasma_job.py').write_text(STUB_SAVE, encoding='utf-8')
        result = self.backend.set_parameters(dict(GOOD, Voltage=30))
        self.assertEqual(result['echo']['Voltage'], 30)
        self.assertEqual(self.calls(), ['supply --app-dir /app --tools-dir %s --port /dev/RS485 --set-voltage 30 --execute'
                                        % self.temp.name, 'file set_parameters'])
        answer = self.backend.write_set_voltage(250)
        self.assertEqual((answer['changed'], answer['set_voltage_v'], answer['previous_set_voltage_v']),
                         (True, 250, 1000.0))
        self.assertIn('--set-voltage 250 --execute', self.calls()[-1])

    def test_a_supply_that_does_not_take_the_voltage_leaves_the_file_alone(self):
        (Path(self.temp.name) / 'plasma_job.py').write_text(STUB_SAVE, encoding='utf-8')
        (Path(self.temp.name) / 'supply-refuses').write_text('', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'The high voltage power supply does not accept the written set voltage. The set voltage on the 100 V'):
            self.backend.set_parameters(GOOD)
        self.assertEqual([line.split()[0] for line in self.calls()], ['supply'])       # the file was never touched

    def test_without_the_measuring_program_the_verified_tool_saves_as_it_always_did(self):
        (Path(self.temp.name) / 'plasma_job.py').write_text(STUB_SAVE, encoding='utf-8')
        self.backend.flow_errors = {'unified': 'RPi There is something attached to plasma_measure.py'}
        self.assertEqual(self.backend.set_parameters(GOOD)['echo'], GOOD)
        self.assertEqual(self.calls(), ['file set_parameters'])
        with self.assertRaisesRegex(RuntimeError, 'The measurement program cannot be found'):
            self.backend.write_set_voltage(30)
        self.assertEqual(self.calls(), ['file set_parameters'])

    def test_tool_errors_are_reported_in_plain_words(self):
        with self.assertRaisesRegex(RuntimeError, 'Before clearing the fault, you need HSPY OFF'):
            self.backend.clear_fault()
        with self.assertRaisesRegex(RuntimeError, 'RS485 is being used by another program'):
            self.backend.stop()

    def test_plasma_command_line_and_single_terminate(self):
        runner, seen = tcpd.Runner(), []
        original = runner.run

        def spy(args, **kwargs):
            seen.append(args)
            kwargs.update(timeout=.5, grace=5)
            return original(args, **kwargs)
        runner.run = spy
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            self.backend.plasma_run('/out/HMI-1', runner, None, 'verified')
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(seen[0][2:], [str(Path(self.temp.name) / 'plasma_job.py'), 'plasma_run', '--app-dir', '/app',
                                       '--tools-dir', self.temp.name, '--port', '/dev/RS485', '--outdir',
                                       '/out/HMI-1', '--execute', '--confirm-high-voltage'])
        self.assertTrue(runner.term_sent)
        self.assertFalse(runner.terminate())
        self.assertFalse(runner.term_repeated)  # it ended at the first signal: nothing to repeat

    def test_which_tools_may_be_signalled_a_second_time(self):
        seen = []

        class Recorder:
            def run(self, args, **kwargs):
                seen.append(kwargs)
                return 0, json.dumps({'valid': True}), '', False, False
        self.backend.plasma_run('/out/HMI-1', Recorder(), None, 'verified')
        self.assertIs(seen[-1]['repeat'](), True)        # plasma_job.py leaves no sign of where it is
        self.backend.spectrum('/out/CAP-1', 100, Recorder(), None)
        self.assertIsNone(seen[-1]['repeat'])            # no high voltage: never signalled twice
        with tempfile.TemporaryDirectory() as folder:
            self.backend.flow_errors = {}                # as if plasma_measure.py lay beside the server
            self.backend.plasma_run(folder, Recorder(), None, 'unified')
            again = seen[-1]['repeat']
            self.assertIs(again(), True)                 # no stage file yet
            for stage, wanted in (('prepare', True), ('signal', True), ('off', False), ('off\n', False)):
                (Path(folder) / 'stage').write_text(stage, encoding='ascii')
                self.assertIs(again(), wanted, stage)


class PretendProcess:
    """In place of subprocess.Popen: a tool that stays until it has had ``needs`` SIGTERMs (None: for ever)."""

    def __init__(self, needs):
        self.needs, self.signals, self.killed, self.returncode = needs, [], False, None

    def __call__(self, *_args, **_kwargs):
        return self

    def poll(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals.append((time.monotonic(), signum))
        if self.needs is not None and len(self.signals) >= self.needs:
            self.returncode = 2

    def kill(self):
        self.killed, self.returncode = True, -9

    def communicate(self, _stdin=None, timeout=None):
        if self.returncode is None:
            time.sleep(min(timeout or .05, .05))
            raise tcpd.subprocess.TimeoutExpired('tool', timeout)
        return '', ''


class RepeatedStopSignalTests(unittest.TestCase):
    """A stop that the tool lost is sent once more; a tool that shows it is switching off is left alone."""

    def setUp(self):
        self.addCleanup(setattr, tcpd, 'REPEAT_TERM_SECONDS', tcpd.REPEAT_TERM_SECONDS)
        self.addCleanup(setattr, tcpd.subprocess, 'Popen', tcpd.subprocess.Popen)
        tcpd.REPEAT_TERM_SECONDS = .4

    def run_tool(self, needs, repeat, stop_after=.1, grace=1.5):
        process = PretendProcess(needs)
        tcpd.subprocess.Popen = process
        runner = tcpd.Runner()
        timer = threading.Timer(stop_after, runner.terminate)
        timer.start()
        _code, _out, _err, _timed_out, killed = runner.run(['tool'], timeout=30, grace=grace, repeat=repeat)
        timer.join()
        return process, runner, killed

    def test_a_tool_that_lost_the_first_signal_gets_one_more_and_never_a_third(self):
        process, runner, killed = self.run_tool(needs=2, repeat=lambda: True)
        self.assertEqual(len(process.signals), 2)
        self.assertGreaterEqual(process.signals[1][0] - process.signals[0][0], .39)
        self.assertEqual((runner.term_repeated, killed), (True, False))
        process, runner, killed = self.run_tool(needs=None, repeat=lambda: True)   # nothing helps: the last resort
        self.assertEqual((len(process.signals), process.killed, killed), (2, True, True))

    def test_a_tool_that_is_switching_off_is_not_signalled_again(self):
        process, _runner, killed = self.run_tool(needs=None, repeat=lambda: False)
        self.assertEqual((len(process.signals), killed), (1, True))

    def test_a_tool_that_is_never_signalled_twice(self):
        process, _runner, _killed = self.run_tool(needs=None, repeat=None)
        self.assertEqual(len(process.signals), 1)

    def test_a_tool_that_ended_at_the_first_signal(self):
        process, runner, killed = self.run_tool(needs=1, repeat=lambda: True)
        self.assertEqual((len(process.signals), runner.term_repeated, killed), (1, False, False))

    def test_the_real_tools_take_a_second_signal_without_harm(self):
        """plasma_measure.on_signal raises for the first signal only; plasma_job ignores signals once its
        OFF sequence has begun. Both together are what makes the repeat safe."""
        import simulated_rig  # supplies the fcntl stand-in plasma_job needs on Windows
        verified = simulated_rig.load_verified()
        interrupts = verified.Interrupts()
        self.addCleanup(setattr, plasma_measure, 'verified', plasma_measure.verified)
        plasma_measure.verified = types.SimpleNamespace(INTERRUPTS=interrupts)
        with self.assertRaises(RuntimeError):
            plasma_measure.on_signal(15, None)
        plasma_measure.on_signal(15, None)               # the repeat: written down, nothing raised
        self.assertEqual(interrupts.received, [15, 15])
        with self.assertRaises(RuntimeError):
            plasma_measure.stop_requested()              # ... and found at the next look
        cleaning = verified.Interrupts()
        cleaning.cleaning = True
        cleaning.handler(15, None)                       # plasma_job in its OFF sequence: ignored
        self.assertEqual(cleaning.received, [15])


class ResultFileTests(unittest.TestCase):
    def test_incomplete_or_failed_jobs_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with self.assertRaisesRegex(ValueError, 'job.json'):
                tcpd.load_plasma_result(directory)
            (directory / 'job.json').write_text(json.dumps({'valid': False, 'error': 'Nano Fault: JOB_TIMEOUT'}),
                                                encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'JOB_TIMEOUT'):
                tcpd.load_plasma_result(directory)
            frames = [{'file': 'spectrum-1.csv'}, {'file': 'spectrum-3.csv'}]
            (directory / 'job.json').write_text(json.dumps({
                'valid': True, 'background': {'valid': True, 'frames': frames},
                'signal': {'valid': True, 'frames': frames}}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                tcpd.load_plasma_result(directory)

    def test_the_report_of_the_longest_job_is_read(self):
        """400 loops is the most that is accepted; the measuring program's report for them is several
        hundred kB (each loop: a trigger and a frame). 300 kB was the limit before."""
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            frames = [{'file': f'spectrum-{index}.csv', 'elapsed_s': 0.03051234, 'minimum': 670.00000001,
                       'maximum': 3711.33333333, 'captured_as': f'spectrum-{index + 1}.csv'} for index in range(1, 401)]
            triggers = [{'frame': index, 'go_utc': '2026-10-06T03:00:00.000000+00:00', 'edge_utc': '2026-10-06T03:00:00.005000+00:00',
                         'taken_ms': 4.1, 'lead_ms': 5.1, 'exposure_before_pulse_ms': 4.2, 'exposure_ms': 30.9}
                        for index in range(1, 401)]
            report = {'valid': True, 'flow': 'unified', 'triggers': triggers, 'steps': ['x' * 60] * 8,
                      'background': {'valid': True, 'frames': frames[:1]}, 'signal': {'valid': True, 'frames': frames}}
            # padded to about 1 MB: more than any legal job writes, and well over the old limit
            (directory / 'job.json').write_text(json.dumps(dict(report, padding='x' * 800000), indent=2), encoding='utf-8')
            self.assertGreater((directory / 'job.json').stat().st_size, 900000)
            with self.assertRaisesRegex(ValueError, 'Spectral file does not exist'):      # it got as far as the frames themselves
                tcpd.load_plasma_result(directory)
            (directory / 'job.json').write_text(json.dumps(dict(report, padding='x' * 2000000)), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'too big'):
                tcpd.load_plasma_result(directory)


class FlowTests(Base):
    """There is only one measurement process at a time: plasma_measure.py (unified). Nothing in a command chooses it."""

    def run_to_the_end(self, client, **fields):
        started = client.request('plasma_run', confirm_high_voltage=True, **fields)
        phases = []
        job = client.wait_job(lambda view: phases.append(view['progress']['phase']), poll_seconds=.02)
        return started, job, [phase for index, phase in enumerate(phases) if index == 0 or phase != phases[index - 1]]

    def test_the_server_says_how_it_measures(self):
        client = self.client()
        self.assertEqual(client.hello['flow'], 'unified')
        status = client.request('status', fresh=True)
        self.assertEqual(status['flow'], 'unified')
        self.assertEqual(status['start_s'], 5)                      # 1000 ms integration: about 3.6 s and that frame
        self.assertGreater(status['estimate_s'], status['start_s'])
        client.request('set_parameters', parameters=dict(GOOD, Integration_Time=4000, Spectra_Interval=4000))
        self.assertEqual(client.request('status')['start_s'], 8)    # the whole background frame is waited for, supply on
        client.request('set_parameters', parameters=dict(GOOD, Integration_Time=100, Spectra_Interval=1000))
        self.assertEqual(client.request('status')['start_s'], 4)    # START takes longer than so short a frame

    def test_one_background_frame_then_the_discharge(self):
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=4))
        started, job, phases = self.run_to_the_end(client)
        self.assertEqual((started['flow'], job['flow'], job['state'], job['error']), ('unified', 'unified', 'done', ''))
        order = ['prepare', 'ramp', 'background', 'signal', 'off', 'finishing']   # the supply first, then the background
        seen = [phase for phase in phases if phase in order]
        self.assertEqual(seen, sorted(set(seen), key=order.index))  # whatever was caught came in this order
        self.assertIn('signal', seen)
        self.assertEqual(job['steps'], ['Nano config Read back', 'spectrometer is ready (background 1 open＋signal 4 open) ',
                                        'HSPY ON,voltage has arrived (no pulse yet)', 'The background spectrum begins to be exposed (high voltage is on, but there is no pulse yet)',
                                        'Nano WAIT_TRIGGER', 'Nano DONE, Spectrum completed'])
        self.assertGreater(job['timing']['wait_s'], 0)
        self.assertTrue(5.0 <= job['timing']['lead_ms'][0] <= job['timing']['lead_ms'][1] <= 6.5)
        result = client.request('result', frames=True)
        self.assertEqual((result['flow'], result['signal_frames'], result['background_frames']), ('unified', 4, 1))
        frames = result['timing']['frames']
        self.assertEqual([frame['frame'] for frame in frames], [1, 2, 3, 4])
        self.assertTrue(all(5.0 <= frame['lead_ms'] <= 6.5 and frame['exposure_ms'] > 1000 for frame in frames))
        self.assertIsNone(frames[0]['period_ms'])
        self.assertTrue(all(frame['period_ms'] > 0 for frame in frames[1:]))
        peak = result['axis'][max(range(1880), key=lambda i: result['difference'][i])]
        self.assertAlmostEqual(peak, 337.13, delta=.3)
        # the second run finds the parameters already in the Nano
        _started, job, _phases = self.run_to_the_end(client)
        self.assertEqual(job['steps'][0], 'Nano config checked, same as the configuration file')
        status = client.request('status')
        self.assertEqual((status['snapshot']['power']['run_stop_setting'], status['busy']), (0, False))

    def test_another_set_voltage_on_the_supply_is_no_reason_and_is_written_by_the_run(self):
        """The person, 2026-10-07 (1000 V on the supply's panel, 30 V in the program; “cannot start — parameter.json Voltage
        30 V and HSPY set value 1000.0 V inconsistent”): “There should be no error message, just useRS485Change it and enter itvoltage”."""
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=2))
        self.sim.backend.set_voltage = 1000.0
        status = client.request('status', fresh=True)
        self.assertEqual((status['snapshot']['power']['set_voltage_v'], status['snapshot']['parameter']['voltage_v']),
                         (1000.0, 100))
        self.assertEqual(status['ready'], {'plasma': True, 'problems': []})
        _started, job, _phases = self.run_to_the_end(client)
        self.assertEqual((job['state'], job['error']), ('done', ''))
        self.assertEqual(job['steps'][0], 'HSPY The set voltage was originally 1000 V,Already in OFF The login password of 100 V and read back')
        self.assertEqual(self.sim.backend.set_voltage, 100)
        _started, job, _phases = self.run_to_the_end(client)                # nothing to write the second time
        self.assertFalse(any('Set voltage original' in step for step in job['steps']))

    def test_sync_voltage_writes_the_voltage_of_the_parameters_into_the_supply(self):
        client = self.client()
        client.request('set_parameters', parameters=GOOD)
        self.sim.backend.set_voltage = 1000.0
        client.request('status', fresh=True)
        self.assertEqual(client.request('sync_voltage'),
                         {'ok': True, 'changed': True, 'set_voltage_v': 100, 'previous_set_voltage_v': 1000.0})
        status = client.request('status')                                   # read again right after the write
        self.assertEqual(status['snapshot']['power']['set_voltage_v'], 100)
        self.assertEqual(client.request('sync_voltage'),
                         {'ok': True, 'changed': False, 'set_voltage_v': 100, 'previous_set_voltage_v': 100})
        # Only with the supply OFF, and never while a job runs.
        self.sim.backend.set_voltage = 500
        self.sim.backend.run_stop = 1
        try:
            with self.assertRaises(PlasmicError) as caught:
                client.request('sync_voltage')
            self.assertEqual(caught.exception.code, 'tool_failed')
            self.assertEqual(self.sim.backend.set_voltage, 500)
        finally:
            self.sim.backend.run_stop = 0
        client.request('status', fresh=True)
        started = client.request('plasma_run', confirm_high_voltage=True)
        with self.assertRaises(PlasmicError) as caught:
            client.request('sync_voltage')
        self.assertEqual(caught.exception.code, 'busy')
        client.request('stop')
        client.wait_job(poll_seconds=.02)
        self.assertTrue(started['job'])

    def test_a_command_cannot_choose_another_flow(self):
        client = self.client()
        self.assertEqual(client.hello['flow_error'], '')
        started = client.request('plasma_run', confirm_high_voltage=True, flow='verified')
        self.assertEqual(started['flow'], 'unified')
        client.wait_job(poll_seconds=.02)

    def test_a_program_that_still_chooses_an_order_is_told_to_update(self):
        """1.3.0 let the sender choose "quick" or "classic". Such a request is refused, not measured in
        a way its sender did not ask for. (A 1.3.0 PC program does not get this far: protocol 2.)"""
        client = self.client()
        for order in ('classic', 'quick', None):
            with self.assertRaises(PlasmicError) as caught:
                client.request('plasma_run', confirm_high_voltage=True, sequence=order)
            self.assertEqual(caught.exception.code, 'bad_request')
            self.assertIn('The program on the computer is an old version', str(caught.exception))
        status = client.request('status', fresh=True)
        self.assertEqual((status['busy'], status['job'], self.sim.backend.run_stop), (False, None, 0))

    def test_the_estimate_counts_what_one_loop_really_takes(self):
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Spectra_Interval=1000, Integration_Time=100))
        self.assertEqual(client.request('status', fresh=True)['estimate_s'], 4 + 8 + 3)       # 8 loops, 1.0 s apart
        client.request('set_parameters', parameters=dict(GOOD, Spectra_Interval=800, Integration_Time=100))
        self.assertEqual(client.request('status', fresh=True)['estimate_s'], round(4 + 8 * .9 + 3))  # 0.75 s train + 0.15 s

    def test_the_simulator_keeps_the_pace_the_bus_allows(self):
        client = self.client()
        client.request('set_parameters', parameters={'Ontime': 1, 'Offtime': 1, 'Cycle': 5, 'Loopnum': 4,
                                                     'Integration_Time': 20, 'Spectra_Interval': 50, 'Voltage': 100})
        started = client.request('plasma_run', confirm_high_voltage=True)
        job = client.wait_job(poll_seconds=.02)
        self.assertEqual((job['state'], job['steps'][0]), ('done', 'Spectral interval 50 ms is shorter than possible, and the actual round 250 ms'))
        report = json.loads((self.sim.controller.output_dir / started['job'] / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual((report['loop_period_ms'], report['off_confirmed'], report['hv_on_attempted']), (250, True, True))

    def test_a_measurement_does_not_read_everything_again_when_the_poll_has_just_done_so(self):
        reads = []
        original = self.sim.backend.status
        self.sim.backend.status = lambda: reads.append(1) or original()
        client = self.client()
        client.request('status', fresh=True)
        before = len(reads)
        client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual(len(reads), before)           # started on the status of a moment ago
        client.wait_job(poll_seconds=.02)
        client.request('status', fresh=True)
        self.sim.controller.snapshot_mono -= 10        # as if nobody had asked for a while
        before = len(reads)
        client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual(len(reads), before + 1)
        client.wait_job(poll_seconds=.02)


class SlowPollTests(Base):
    """poll_s may be as long as 60 s. A measurement never starts on a status older than 10 s (20 s is refused)."""
    overrides = {'poll_s': 30}

    def test_the_status_a_measurement_starts_on_is_never_old(self):
        reads = []
        original = self.sim.backend.status
        self.sim.backend.status = lambda: reads.append(1) or original()
        client = self.client()
        for age, read_again in ((5, 0), (11, 1), (19, 1), (25, 1)):
            client.request('status', fresh=True)
            self.sim.controller.snapshot_mono -= age
            before = len(reads)
            client.request('plasma_run', confirm_high_voltage=True)   # never "Status exceeds 20 Not updated in seconds"
            self.assertEqual(len(reads) - before, read_again, age)
            client.wait_job(poll_seconds=.02)


class StopWhileStartingTests(Base):
    """A stop that comes while a discharge is being started (the bus is taken, the status is being read,
    there is no job yet to signal) must not be answered "busy" while the discharge goes ahead."""

    def start_slowly_and_stop(self, flow_reads_first):
        controller, backend = self.sim.controller, self.sim.backend
        client = self.client()
        client.request('status', fresh=True)
        if flow_reads_first is False:
            controller.snapshot_mono -= 15             # unified: only an old status is read again
        original = backend.status
        on_seen = []

        def slow_status():
            time.sleep(.9)                             # as long as one read takes on the RPi
            return original()
        backend.status = slow_status
        outcome = {}

        def start():
            try:
                outcome['reply'] = client.request('plasma_run', confirm_high_voltage=True)
            except PlasmicError as exc:
                outcome['error'] = (exc.code, str(exc))
        starter = threading.Thread(target=start)
        starter.start()
        time.sleep(.3)                                 # the start holds the bus and is reading the status
        began = time.monotonic()
        watcher_done = threading.Event()

        def watch():
            while not watcher_done.wait(.01):
                if backend.run_stop:
                    on_seen.append(1)
        watcher = threading.Thread(target=watch)
        watcher.start()
        reply = client_stop('127.0.0.1', self.sim.port)
        took = time.monotonic() - began
        starter.join(20)
        backend.status = original
        time.sleep(.5)
        watcher_done.set()
        watcher.join()
        return outcome, reply, took, on_seen

    def check(self, outcome, reply, took, on_seen):
        self.assertEqual(outcome.get('error', ('', ''))[0], 'cancelled', outcome)
        self.assertIn('A stop command was received during startup and discharge did not start.', outcome['error'][1])
        self.assertEqual((reply['ok'], reply.get('stopped')), (True, True), reply)   # and the stop itself was carried out
        self.assertLess(took, 6)
        self.assertEqual(on_seen, [])                  # the supply never came on
        self.assertIsNone(self.sim.controller.running_job())
        self.assertEqual(self.sim.backend.run_stop, 0)

    def test_stop_during_the_status_read_of_a_start(self):
        self.check(*self.start_slowly_and_stop(flow_reads_first=False))

    def test_an_earlier_stop_does_not_cancel_a_later_start(self):
        client = self.client()
        client_stop('127.0.0.1', self.sim.port)
        client.request('status', fresh=True)
        self.assertTrue(client.request('plasma_run', confirm_high_voltage=True)['job'])
        client.wait_job(poll_seconds=.02)

    def test_a_stop_that_finds_the_bus_busy_looks_for_the_job_once_more(self):
        controller = self.sim.controller
        job = tcpd.Job('plasma', controller.output_dir / ('HMI-' + '0' * 32), tcpd.Connection('127.0.0.1'))
        answers = [None, job, job]                     # no job when the stop arrives; one by the time it asks for the bus
        controller.running_job = lambda: answers.pop(0) if answers else job
        reply = controller.stop()
        self.assertEqual((reply['cancel_sent'], reply['job'], job.runner.term_sent, job.cancel_reason),
                         (True, job.id, True, 'stop'))

    def test_a_stop_that_did_not_get_through_is_on_record(self):
        controller = self.sim.controller

        def busy():
            raise tcpd.Failure('busy', 'RS485 is executing other commands, please try again later.')
        controller._acquire_bus = busy
        with self.assertRaises(tcpd.Failure):
            controller.handle(tcpd.Connection('127.0.0.1'), {'cmd': 'stop'})
        last = json.loads(controller.audit_path.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((last['cmd'], last['ok']), ('stop', False))
        self.assertIn('RS485 is executing other commands', last['detail'])


class StopWhileStartingVerifiedTests(StopWhileStartingTests):
    """The verified flow reads the status before every start: the same must hold there."""
    overrides = {'flow': 'verified'}

    def test_stop_during_the_status_read_of_a_start(self):
        self.check(*self.start_slowly_and_stop(flow_reads_first=True))


class WatchdogOriginTests(Base):
    overrides = {'watchdog_s': 2}

    def test_the_watchdog_counts_from_the_start_of_the_job(self):
        """The owner cannot say anything while it waits for the answer to its plasma_run. A start that
        took longer than watchdog_s (a busy bus, a slow status read) must not be stopped at once."""
        controller = self.sim.controller
        owner = tcpd.Connection('127.0.0.1')
        owner.last_seen = time.monotonic() - 10        # its plasma_run line came 10 s ago
        job = tcpd.Job('plasma', controller.output_dir / ('HMI-' + '1' * 32), owner)
        controller._tick(job)
        self.assertFalse(job.runner.term_sent)
        job.started_mono -= 2.5                        # ... and now it has been silent since the job began
        controller._tick(job)
        self.assertEqual((job.runner.term_sent, job.cancel_reason), (True, 'watchdog_timeout'))


class VerifiedFlowTests(Base):
    """The way back: "flow": "verified" in the server's config.json runs plasma_job.py as it always was."""
    overrides = {'flow': 'verified'}

    def test_the_verified_tool_keeps_its_three_background_frames(self):
        client = self.client()
        self.assertEqual(client.hello['flow'], 'verified')
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=3))
        status = client.request('status', fresh=True)
        self.assertEqual((status['flow'], status['start_s']), ('verified', 11))
        reads = []
        original = self.sim.backend.status
        self.sim.backend.status = lambda: reads.append(1) or original()
        started = client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual((started['flow'], len(reads)), ('verified', 1))  # it reads first, as it always did
        phases = []
        job = client.wait_job(lambda view: phases.append(view['progress']['phase']), poll_seconds=.02)
        self.assertEqual((job['flow'], job['state']), ('verified', 'done'))
        self.assertEqual(job['steps'][1], 'background spectrum completed')
        self.assertLess(phases.index('background'), phases.index('signal'))
        result = client.request('result')
        self.assertEqual((result['flow'], result['background_frames'], result['signal_frames']), ('verified', 3, 3))
        timing = result['timing']
        self.assertEqual((timing['flow'], len(timing['frames'])), ('verified', 3))
        self.assertTrue(all(frame['lead_ms'] is None for frame in timing['frames']))  # that tool does not note it
        self.assertGreater(timing['wait_s'], 0)

    def test_the_fall_back_still_refuses_another_set_voltage_until_it_is_written(self):
        """plasma_job.py checks the set voltage itself and writes nothing: in this flow the difference stays a reason,
        and sync_voltage (or saving the parameters) takes it away."""
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=2))
        self.sim.backend.set_voltage = 1000.0
        status = client.request('status', fresh=True)
        self.assertEqual([row['reason'] for row in status['ready']['problems']],
                         ['parameter.json Voltage 100 V and HSPY set value 1000.0 V inconsistent'])
        with self.assertRaises(PlasmicError) as caught:
            client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual(caught.exception.code, 'not_ready')
        self.assertTrue(client.request('sync_voltage')['changed'])
        self.assertEqual(client.request('status')['ready'], {'plasma': True, 'problems': []})

    def test_settings_file(self):
        self.assertEqual(tcpd.load_config(None)['flow'], 'unified')
        with self.assertRaises(ValueError):
            tcpd.load_config(None, {'flow': 'quick'})


class StopBeforeTheFirstPulseTests(Base):
    speed = 4
    overrides = {'watchdog_s': 2}

    def test_stop_while_the_background_is_taken(self):
        """The supply is on by then, and no pulse has been sent."""
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=1, Integration_Time=6000, Spectra_Interval=6000))
        started = client.request('plasma_run', confirm_high_voltage=True)
        wait_until(lambda: client.request('job')['job']['progress']['phase'] == 'background')
        self.assertEqual((self.sim.backend.run_stop, self.sim.backend.pulses), (1, 0))
        reply = client_stop('127.0.0.1', self.sim.port)
        self.assertTrue(reply['cancel_sent'])
        job = client.wait_job(poll_seconds=.05)
        self.assertEqual((job['state'], job['cancel_reason'], job['post_stop']), ('cancelled', 'stop', {'ok': True, 'error': ''}))
        self.assertEqual((self.sim.backend.run_stop, self.sim.backend.voltage, self.sim.backend.pulses), (0, 0.0, 0))
        report = json.loads((self.sim.controller.output_dir / started['job'] / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual((report['hv_on_attempted'], report['off_confirmed'], 'signal' in report), (True, True, False))

    def test_stop_before_the_supply_is_on(self):
        client = self.client()
        client.request('set_parameters', parameters=dict(GOOD, Loopnum=1))
        self.sim.backend.speed = .5        # the first steps take 2-3 s: the stop below comes well inside them
        started = client.request('plasma_run', confirm_high_voltage=True)
        self.assertEqual(client.request('job')['job']['progress']['phase'], 'prepare')
        reply = client_stop('127.0.0.1', self.sim.port)
        self.assertTrue(reply['cancel_sent'])
        job = client.wait_job(poll_seconds=.05)
        self.assertEqual((job['state'], job['cancel_reason']), ('cancelled', 'stop'))
        report = json.loads((self.sim.controller.output_dir / started['job'] / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual((report['hv_on_attempted'], 'ramp' in report['timeline_s']), (False, False))
        self.assertEqual((self.sim.backend.run_stop, self.sim.backend.voltage), (0, 0.0))

    def test_a_lost_owner_stops_the_discharge(self):
        owner = self.client()
        owner.request('set_parameters', parameters=dict(GOOD, Loopnum=40))
        owner.request('plasma_run', confirm_high_voltage=True)

        def supply_on():
            owner.request('ping')
            return self.sim.backend.run_stop == 1
        wait_until(supply_on)
        owner.close()
        job = wait_until(lambda: self.sim.controller.job if self.sim.controller.job.state != 'running' else None)
        self.assertEqual((job.state, job.cancel_reason), ('cancelled', 'owner_disconnected'))
        self.assertEqual((self.sim.backend.run_stop, self.sim.backend.voltage), (0, 0.0))


class JobTimingTests(unittest.TestCase):
    """job_timing() on files as the two real tools write them."""

    def events(self, directory, rows):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'progress.jsonl').open('w', encoding='utf-8') as stream:
            for clock, stage, details in rows:
                stream.write(json.dumps({'utc': f'2026-10-05T14:00:{clock}+00:00', 'stage': stage, **details}) + '\n')

    def test_the_measuring_program_reports_the_measured_lead(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            # its spectrometer log numbers the background as frame 1; only what the program noted itself is used
            self.events(directory / 'frames', [('03.700000', 'frame_triggered', {'frame': 1}),
                                               ('05.510000', 'frame_triggered', {'frame': 2})])
            report = {'flow': 'unified', 'started_at': '2026-10-05T14:00:02.180000+00:00',
                      'timeline_s': {'hspy_on': 1.6, 'first_pulse': 3.34},
                      'triggers': [{'frame': 1, 'capture_frame': 2, 'edge_utc': '2026-10-05T14:00:05.516900+00:00',
                                    'exposure_before_pulse_ms': 5.5, 'exposure_ms': 1021.0},
                                   {'frame': 2, 'capture_frame': 3, 'edge_utc': '2026-10-05T14:00:07.023200+00:00',
                                    'exposure_before_pulse_ms': 6.0, 'exposure_ms': 1010.5}]}
            timing = tcpd.job_timing(directory, report, '2026-10-05T14:00:02.000000+00:00')
            self.assertEqual((timing['flow'], timing['wait_s'], timing['lead_ms']), ('unified', 3.52, [5.5, 6.0]))
            self.assertEqual(timing['frames'], [
                {'frame': 1, 'lead_ms': 5.5, 'exposure_ms': 1021.0, 'period_ms': None},
                {'frame': 2, 'lead_ms': 6.0, 'exposure_ms': 1010.5, 'period_ms': 1506}])
            self.assertEqual(timing['steps_s'], report['timeline_s'])
            # without the time of the command: counted from the start of the tool
            self.assertEqual(tcpd.job_timing(directory, report)['wait_s'], 3.34)

    def test_the_verified_tool_only_leaves_when_its_spectrometer_program_was_told(self):
        shot = 'UAI_SpectrometerDataOneshot'
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.events(directory / 'signal', [
                ('10.320000', 'frame_triggered', {'frame': 1}),
                ('10.320800', 'sdk_call_start', {'function': shot, 'frame': 1}),
                ('11.341800', 'sdk_call_done', {'function': shot, 'frame': 1, 'code': 0}),
                ('12.268000', 'frame_triggered', {'frame': 2}),
                ('12.269000', 'sdk_call_start', {'function': shot, 'frame': 2})])
            report = {'started_at': '2026-10-05T14:00:00.180000+00:00', 'steps': []}
            timing = tcpd.job_timing(directory, report, '2026-10-05T14:00:00+00:00')
            self.assertEqual((timing['flow'], timing['wait_s']), ('verified', 10.32))
            self.assertNotIn('lead_ms', timing)
            self.assertEqual(timing['frames'], [
                {'frame': 1, 'lead_ms': None, 'exposure_ms': 1021.0, 'period_ms': None},
                {'frame': 2, 'lead_ms': None, 'exposure_ms': None, 'period_ms': 1948}])

    def test_nothing_to_read_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(tcpd.job_timing(Path(folder), {}), {'flow': 'verified', 'wait_s': None, 'frames': []})
            (Path(folder) / 'signal').mkdir()
            (Path(folder) / 'signal' / 'progress.jsonl').write_text('not json\n{"stage": "frame_triggered"}\n', encoding='utf-8')
            self.assertEqual(tcpd.job_timing(Path(folder), {'triggers': 'x'})['frames'], [])


class MeasureToolPlumbingTests(unittest.TestCase):
    def test_the_measuring_program_is_run_with_the_same_confirmations(self):
        backend = tcpd.RealBackend({'app_dir': '/app', 'tools_dir': '/tools', 'serial_port': '/dev/RS485'})
        self.assertEqual(backend.flow_errors, {})
        seen = []

        class Recorder:
            def run(self, args, **kwargs):
                seen.append((args, kwargs))
                return 0, json.dumps({'valid': True, 'steps': ['x']}), '', False, False
        self.assertEqual(backend.plasma_run('/out/HMI-2', Recorder(), None)['steps'], ['x'])
        args, kwargs = seen[0]
        self.assertEqual(args[2:], [str(ROOT / 'rpi' / 'tcpd' / 'plasma_measure.py'), '--app-dir', '/app', '--tools-dir', '/tools',
                                    '--port', '/dev/RS485', '--outdir', '/out/HMI-2', '--lead-ms', '5',
                                    '--execute', '--confirm-high-voltage'])
        self.assertEqual((kwargs['timeout'], kwargs['grace']), (215, 60))  # the same limits as the verified tool
        backend.plasma_run('/out/HMI-3', Recorder(), None, 'verified')
        self.assertEqual(seen[1][0][2:4], [str(Path('/tools') / 'plasma_job.py'), 'plasma_run'])

    def test_the_exposure_lead_comes_from_the_settings_file(self):
        self.assertEqual(tcpd.load_config(None)['exposure_lead_ms'], 5)
        for bad in (True, '5', None, -1, 200.5, 1000):
            with self.assertRaises(ValueError, msg=repr(bad)):
                tcpd.load_config(None, {'exposure_lead_ms': bad})
        seen = []

        class Recorder:
            def run(self, args, **kwargs):
                seen.append(args)
                return 0, json.dumps({'valid': True}), '', False, False
        for value, written in ((0, '0'), (12.5, '12.5'), (200, '200')):
            config = tcpd.load_config(None, {'exposure_lead_ms': value})
            tcpd.RealBackend(config).plasma_run('/out/HMI-4', Recorder(), None)
            self.assertEqual(seen[-1][seen[-1].index('--lead-ms') + 1], written)
        # the program takes exactly what the server writes
        self.assertEqual(plasma_measure.LONGEST_LEAD_SECONDS * 1000, 200)

    def server_alone(self, folder, tool_text=None, **settings):
        """The server program in a folder of its own, with or without a plasma_measure.py beside it."""
        copy = Path(folder) / 'plasmic_tcpd.py'
        copy.write_bytes((ROOT / 'rpi' / 'tcpd' / 'plasmic_tcpd.py').read_bytes())
        if tool_text is not None:
            (Path(folder) / 'plasma_measure.py').write_text(tool_text, encoding='utf-8')
        import importlib.util
        spec = importlib.util.spec_from_file_location('plasmic_tcpd_alone', copy)
        alone = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(alone)
        backend = alone.RealBackend({'app_dir': '/app', 'tools_dir': '/tools', 'serial_port': '/dev/RS485'})
        config = alone.load_config(None, dict({'output_dir': str(Path(folder) / 'captures')}, **settings))
        controller = alone.Controller(config, backend)
        self.addCleanup(controller.begin_shutdown)
        return alone, backend, controller

    def test_a_server_without_the_measuring_program_refuses_to_discharge(self):
        """It says why, and never measures with the verified tool instead: nobody chose that."""
        for tool_text, word in ((None, 'not found'), ('def broken(:\n', 'cannot be loaded')):
            with self.subTest(word=word), tempfile.TemporaryDirectory() as folder:
                alone, backend, controller = self.server_alone(folder, tool_text)
                self.assertIn(word, backend.flow_errors['unified'])
                self.assertEqual(controller.flow, 'unified')
                self.assertIn('plasma_measure.py', controller.flow_error)
                connection = alone.Connection('127.0.0.1')
                self.assertEqual(controller.hello(connection)['flow_error'], controller.flow_error)
                view = controller.status_view()
                self.assertFalse(view['ready']['plasma'])
                self.assertIn(controller.flow_error, [row['reason'] for row in view['ready']['problems']])
                with self.assertRaises(alone.Failure) as caught:
                    controller.start_job(connection, 'plasma')
                self.assertEqual((caught.exception.code, str(caught.exception)), ('not_ready', controller.flow_error))
                self.assertFalse(controller.bus.locked())
                started = []
                runner = alone.Runner()
                runner.run = lambda *args, **kwargs: started.append(args)   # no process may even be started
                with self.assertRaisesRegex(RuntimeError, 'Please re-execute on the computer Install.cmd'):
                    backend.plasma_run(Path(folder) / 'HMI-x', runner, None, 'unified')
                self.assertEqual(started, [])

    def test_the_verified_flow_does_not_need_the_measuring_program(self):
        with tempfile.TemporaryDirectory() as folder:
            _alone, _backend, controller = self.server_alone(folder, flow='verified')
            self.assertEqual((controller.flow, controller.flow_error), ('verified', ''))

    def test_a_measuring_program_that_is_there_is_taken(self):
        with tempfile.TemporaryDirectory() as folder:
            _alone, backend, controller = self.server_alone(folder, 'print("x")\n')
            self.assertEqual((backend.flow_errors, controller.flow_error), ({}, ''))

if __name__ == '__main__':
    unittest.main(verbosity=2)
