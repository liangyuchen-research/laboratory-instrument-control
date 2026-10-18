"""One-click installer and automatic test of connection check (not required RPi) .

    python tests/test_installer.py

scp/ssh That section requires one SSH server is not tested here; what is measured here is: the judgment of the connection check,
How to prepare the folder to be uploaded?RPi The section in the installation script responsible for writing the configuration file Python,and the format of each script file.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'rpi' / 'tcpd'), str(ROOT / 'pc'), str(ROOT / 'tests'), str(ROOT / 'installer')]

import install  # noqa: E402
import netcheck  # noqa: E402
import plasmic_tcpd as tcpd  # noqa: E402
from test_tcpd import SimServer  # noqa: E402


class NetcheckTests(unittest.TestCase):
    def test_same_subnet(self):
        self.assertTrue(netcheck.same_subnet('192.168.1.2', '192.168.1.1'))
        self.assertFalse(netcheck.same_subnet('192.168.0.2', '192.168.1.1'))
        self.assertFalse(netcheck.same_subnet('169.254.82.241', '192.168.1.1'))
        self.assertFalse(netcheck.same_subnet('192.168.1.2', 'raspberrypi'))

    def test_probe_tells_the_cases_apart(self):
        sim = SimServer(token='the-right-token')
        self.addCleanup(sim.close)
        good = netcheck.probe('127.0.0.1', sim.port, 'the-right-token')
        self.assertEqual((good['tcp'], good['authed'], good['code'], good['hello']['server']),
                         (True, True, '', 'plasmic-tcpd'))
        self.assertEqual(netcheck.probe('127.0.0.1', sim.port, 'wrong')['code'], 'auth_failed')
        self.assertEqual(netcheck.probe('127.0.0.1', sim.port, '')['code'], 'auth_required')
        refused = netcheck.probe('127.0.0.1', 9, 'x', timeout=2)
        self.assertEqual((refused['tcp'], refused['authed']), (False, False))
        self.assertIn(refused['code'], ('refused', 'timeout', 'unreachable'))
        self.assertIsNone(sim.controller.job)  # probing never starts anything

    def test_probe_reports_a_forbidden_address(self):
        sim = SimServer(allow=['192.168.1.2'])
        self.addCleanup(sim.close)
        found = netcheck.probe('127.0.0.1', sim.port, '')
        self.assertEqual((found['tcp'], found['authed'], found['code']), (True, False, 'forbidden'))
        self.assertIn('Allow list', found['error'])

    def test_diagnose_walks_from_computer_to_equipment(self):
        sim = SimServer(token='t0ken')
        self.addCleanup(sim.close)
        device = {'snapshot': {'power': {'valid': True}, 'nano': {'valid': False, 'error': 'Nano reply missing'}}}
        steps = {step['key']: step for step in netcheck.diagnose('127.0.0.1', sim.port, 't0ken', device)}
        self.assertEqual(list(steps), ['ip', 'ping', 'port', 'hello', 'token', 'device'])
        self.assertEqual([steps[key]['ok'] for key in ('ip', 'ping', 'port', 'hello', 'token')], [True] * 5)
        self.assertFalse(steps['device']['ok'])
        self.assertIn('Nano Can’t read', steps['device']['detail'])
        wrong = {step['key']: step for step in netcheck.diagnose('127.0.0.1', sim.port, 'nope', device)}
        self.assertEqual((wrong['hello']['ok'], wrong['token']['ok'], wrong['device']['ok']), (True, False, None))
        self.assertIn('Install.cmd', wrong['token']['fix'])
        closed = {step['key']: step for step in netcheck.diagnose('127.0.0.1', 9, '', None)}
        self.assertEqual((closed['port']['ok'], closed['hello']['ok'], closed['token']['ok']), (False, None, None))
        self.assertIn('Install.cmd', closed['port']['fix'])

    def test_diagnose_explains_a_missing_address(self):
        original = netcheck.local_ipv4, netcheck.ping
        netcheck.local_ipv4, netcheck.ping = (lambda: ['169.254.82.241']), (lambda host, timeout_ms=1000: False)
        self.addCleanup(lambda: setattr(netcheck, 'local_ipv4', original[0]) or setattr(netcheck, 'ping', original[1]))
        steps = {step['key']: step for step in netcheck.diagnose('192.168.1.1', 9, '', None)}
        self.assertEqual((steps['ip']['ok'], steps['ping']['ok'], steps['port']['ok']), (False, False, None))
        self.assertIn('192.168.1.2', steps['ip']['fix'])
        self.assertIn('169.254.82.241', steps['ip']['detail'])


class Patching(unittest.TestCase):
    def patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)


class FindTests(Patching):
    """“automatic search RPi” on the computer side.

    Nothing here leaves this computer: the list of network cards is replaced by an empty one,
    so no broadcast and no knocking reaches the real network.
    """

    def setUp(self):
        self.patch(netcheck, 'local_ipv4', lambda: [])

    def test_discover_asks_the_given_address_and_reads_the_answer(self):
        sim = SimServer(discovery=True, token='t0ken')
        self.addCleanup(sim.close)
        rows = netcheck.discover(sim.port, timeout=1.0, hosts=['127.0.0.1'])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].pop('name'))
        self.assertEqual(rows, [{'host': '127.0.0.1', 'port': sim.port, 'version': tcpd.VERSION,
                                 'sim': True, 'allowed': True}])
        self.assertIsNone(sim.controller.job)

    def test_discover_is_quiet_when_nobody_answers(self):
        started = time.monotonic()
        self.assertEqual(netcheck.discover(9, timeout=.4, hosts=['127.0.0.1']), [])
        self.assertEqual(netcheck.discover(9, timeout=.2), [])  # no network card, nobody to ask
        self.assertLess(time.monotonic() - started, 3)

    def test_discover_ignores_answers_from_anything_else(self):
        stranger = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        stranger.bind(('127.0.0.1', 0))
        stranger.settimeout(3)
        self.addCleanup(stranger.close)

        def answer():
            try:
                _data, sender = stranger.recvfrom(256)
                for reply in (b'not json', b'[1, 2]', b'{"server": "something-else", "port": 1}'):
                    stranger.sendto(reply, sender)
            except OSError:
                pass
        threading.Thread(target=answer, daemon=True).start()
        self.assertEqual(netcheck.discover(stranger.getsockname()[1], timeout=.6, hosts=['127.0.0.1']), [])

    def test_sweep_never_leaves_private_networks(self):
        calls = []
        self.patch(netcheck, '_knock', lambda host, port, timeout, banner: calls.append((host, port)))
        self.assertEqual(netcheck.sweep(22, addresses=['8.8.8.8', '140.112.8.116', '127.0.0.1', 'not-an-ip']), [])
        self.assertEqual(calls, [])  # a computer with a public address: nobody is knocked on
        self.assertEqual(netcheck.sweep(22, addresses=['192.168.77.5']), [])
        self.assertEqual(len(calls), 253)
        self.assertEqual({port for _host, port in calls}, {22})
        self.assertTrue(all(host.startswith('192.168.77.') and host != '192.168.77.5' for host, _port in calls))
        calls.clear()
        netcheck.sweep(22, addresses=['192.168.1.2', '192.168.2.2', '10.0.0.2', '172.16.0.2', '192.168.1.3'])
        self.assertEqual(len({host.rsplit('.', 1)[0] for host, _port in calls}), 3)  # at most three networks

    def test_sweep_reports_who_accepted_in_address_order(self):
        greeting = 'SSH-2.0-OpenSSH_8.4p1 Raspbian-5+deb11u3'
        self.patch(netcheck, '_knock', lambda host, port, timeout, banner:
                   (host, greeting) if host.endswith(('.82', '.9')) else None)
        self.assertEqual(netcheck.sweep(22, banner=True, addresses=['192.168.0.130']),
                         [{'host': '192.168.0.9', 'banner': greeting}, {'host': '192.168.0.82', 'banner': greeting}])

    def test_knock_reads_the_ssh_greeting(self):
        server = socket.create_server(('127.0.0.1', 0))
        port = server.getsockname()[1]

        def greet():
            try:
                while True:
                    connection, _ = server.accept()
                    connection.sendall(b'SSH-2.0-OpenSSH_8.4p1 Raspbian-5+deb11u3\r\n')
                    connection.close()
            except OSError:
                pass
        threading.Thread(target=greet, daemon=True).start()
        self.assertEqual(netcheck._knock('127.0.0.1', port, 1.0, True),
                         ('127.0.0.1', 'SSH-2.0-OpenSSH_8.4p1 Raspbian-5+deb11u3'))
        self.assertTrue(netcheck.port_open('127.0.0.1', port))
        server.close()
        self.assertFalse(netcheck.port_open('127.0.0.1', port, timeout=.5))

    def test_a_raspberry_pi_is_recognised_and_listed_first(self):
        self.patch(netcheck, 'sweep', lambda port, banner=False: [
            {'host': '192.168.0.1', 'banner': 'SSH-2.0-dropbear_2020.81'},
            {'host': '192.168.0.50', 'banner': 'SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3'},
            {'host': '192.168.0.82', 'banner': 'SSH-2.0-OpenSSH_8.4p1 Raspbian-5+deb11u3'}])
        self.patch(netcheck, 'arp_table', lambda: {'192.168.0.50': 'dc-a6-32-11-22-33',
                                                   '192.168.0.1': '00-11-32-aa-bb-cc'})
        rows = netcheck.find_ssh_hosts()
        self.assertEqual([(row['host'], row['rpi']) for row in rows],
                         [('192.168.0.50', True), ('192.168.0.82', True), ('192.168.0.1', False)])
        self.assertEqual((rows[0]['mac'], rows[1]['mac']), ('dc-a6-32-11-22-33', ''))

    def test_the_arp_listing_is_read_whatever_the_windows_language(self):
        text = ('\r\ninterface: 192.168.0.130 --- 0x5\r\n  Internet URL Physical Address Type\r\n'
                '  192.168.0.1           00-11-32-aa-bb-cc     dynamic\r\n'
                '  192.168.0.82          DC-A6-32-11-22-33     dynamic\r\n'
                '  192.168.0.255         ff-ff-ff-ff-ff-ff     static\r\n')
        self.patch(netcheck.subprocess, 'run',
                   lambda *_args, **_options: types.SimpleNamespace(stdout=text.encode('cp950'), returncode=0))
        table = netcheck.arp_table()
        self.assertEqual((table['192.168.0.82'], table['192.168.0.1']), ('dc-a6-32-11-22-33', '00-11-32-aa-bb-cc'))

    def test_deep_search_knocks_only_when_the_question_found_nothing(self):
        knocked = []
        self.patch(netcheck, 'sweep',
                   lambda port, **_options: knocked.append(port) or [{'host': '127.0.0.1', 'banner': ''}])
        silent = SimServer(token='t0ken')  # no UDP answer, as if a firewall swallowed it
        self.addCleanup(silent.close)
        self.assertEqual(netcheck.find_servers(silent.port, deep=False, hosts=['127.0.0.1']), [])
        self.assertEqual(knocked, [])
        rows = netcheck.find_servers(silent.port, deep=True, hosts=['127.0.0.1'])
        self.assertEqual([(row['host'], row['port'], row['version'], row['allowed']) for row in rows],
                         [('127.0.0.1', silent.port, tcpd.VERSION, True)])
        refusing = SimServer(allow=['192.168.1.2'])
        self.addCleanup(refusing.close)
        self.assertEqual([(row['host'], row['allowed']) for row in netcheck.find_servers(refusing.port, deep=True)],
                         [('127.0.0.1', False)])
        knocked.clear()
        answering = SimServer(discovery=True)
        self.addCleanup(answering.close)
        self.assertEqual(len(netcheck.find_servers(answering.port, deep=True, hosts=['127.0.0.1'])), 1)
        self.assertEqual(knocked, [])


class InstallerTargetTests(Patching):
    """Which RPi the installer talks to: installer/site.json, the questions, and the search."""

    def quiet(self, action, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = action(*arguments)
        return result, output.getvalue()

    def site_in(self, folder):
        self.patch(install, 'SITE', Path(folder) / 'site.json')
        return install.SITE

    def test_the_site_file_in_this_folder_is_usable_and_holds_no_password(self):
        raw = json.loads((ROOT / 'installer' / 'site.json').read_text(encoding='utf-8'))
        self.assertEqual(set(raw), {'rpi_host', 'rpi_user'})
        self.assertEqual(install.load_site(), raw)  # both values pass the checks unchanged

    def test_a_missing_or_wrong_site_file_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            site = self.site_in(folder)
            self.assertEqual(install.load_site(), install.BUILT_IN_SITE)
            for text in ('not json', '[1]', '{"rpi_host": "999.1.1.1", "rpi_user": "Bad Name"}',
                         '{"rpi_host": 5, "rpi_user": null}'):
                site.write_text(text, encoding='utf-8')
                self.assertEqual(install.load_site(), install.BUILT_IN_SITE, text)
            site.write_text('{"rpi_host": " 10.0.0.7 "}', encoding='utf-8')
            self.assertEqual(install.load_site(), {'rpi_host': '10.0.0.7', 'rpi_user': 'pi'})

    def test_addresses_are_checked(self):
        for text, good in (('192.168.0.82', True), ('10.0.0.1', True), ('192.168.0.256', False),
                           ('raspberrypi', False), ('1.2.3', False), ('', False), ('1.2.3.4.5', False)):
            self.assertEqual(install.valid_ip(text), good, text)

    def test_what_the_person_typed_is_remembered(self):
        with tempfile.TemporaryDirectory() as folder:
            site = self.site_in(folder)
            install.ARGS = types.SimpleNamespace(host='192.168.1.1', user='pi', yes=False)
            answers = iter(['192.168.0.300', '192.168.0.95', 'Wow!', 'wow'])
            self.patch(install, 'ask', lambda prompt, default='': next(answers))
            self.quiet(install.change_target)
            self.assertEqual((install.ARGS.host, install.ARGS.user), ('192.168.0.95', 'wow'))
            self.assertEqual(json.loads(site.read_text(encoding='utf-8')), {'rpi_host': '192.168.0.95', 'rpi_user': 'wow'})

    def test_search_lists_candidates_and_takes_the_chosen_one(self):
        with tempfile.TemporaryDirectory() as folder:
            self.site_in(folder)
            install.ARGS = types.SimpleNamespace(host='192.168.1.1', user='wow', yes=False, tcp_port=8767, ssh_port=22)
            self.patch(netcheck, 'discover', lambda port, timeout=1.2, hosts=(): [
                {'host': '192.168.0.95', 'port': 8767, 'version': '1.2.0', 'name': 'raspberrypi', 'sim': False,
                 'allowed': True}])
            self.patch(netcheck, 'find_ssh_hosts', lambda port=22: [
                {'host': '192.168.0.95', 'banner': 'SSH-2.0-OpenSSH_8.4p1 Raspbian-5', 'mac': '', 'rpi': True},
                {'host': '192.168.0.82', 'banner': 'SSH-2.0-OpenSSH_8.4p1 Raspbian-5', 'mac': 'dc-a6-32-11-22-33',
                 'rpi': True},
                {'host': '192.168.0.1', 'banner': 'SSH-2.0-dropbear', 'mac': '', 'rpi': False}])
            self.patch(install, 'ask', lambda prompt, default='': '')
            changed, text = self.quiet(install.find_rpi)
            self.assertFalse(changed)
            self.assertEqual(install.ARGS.host, '192.168.1.1')
            self.assertEqual(text.count('192.168.0.95'), 1)  # listed once, as the installed server
            self.assertIn('has been installed Plasmic TCP server 1.2.0', text)
            self.assertIn('It seems Raspberry Pi', text)
            self.assertIn('Not sure if it is RPi', text)
            self.patch(install, 'ask', lambda prompt, default='': '2')
            changed, _ = self.quiet(install.find_rpi)
            self.assertTrue(changed)
            self.assertEqual(install.ARGS.host, '192.168.0.82')
            self.assertEqual(install.load_site(), {'rpi_host': '192.168.0.82', 'rpi_user': 'wow'})

    def test_search_that_finds_nothing_says_what_to_check(self):
        install.ARGS = types.SimpleNamespace(host='192.168.0.82', user='wow', yes=True, tcp_port=8767, ssh_port=22)
        self.patch(netcheck, 'discover', lambda port, timeout=1.2, hosts=(): [])
        self.patch(netcheck, 'find_ssh_hosts', lambda port=22: [])
        changed, text = self.quiet(install.find_rpi)
        self.assertFalse(changed)
        self.assertIn('hostname -I', text)

    def test_network_step_offers_the_search_when_the_rpi_is_silent(self):
        install.ARGS = types.SimpleNamespace(host='192.168.0.82', user='wow', yes=True, tcp_port=8767, ssh_port=22)
        self.patch(install, 'reachable', lambda host: False)
        self.patch(netcheck, 'local_ipv4', lambda: ['192.168.0.130'])
        answered, text = self.quiet(install.step_network)
        self.assertFalse(answered)  # unattended: skip the RPi instead of waiting for ever
        self.assertIn('[F] automatic search RPi', text)
        self.assertIn('192.168.0.130', text)
        self.assertNotIn('[W]', text)  # the computer's address is fine; nothing to set there
        self.patch(netcheck, 'local_ipv4', lambda: ['10.9.8.7'])
        _, text = self.quiet(install.step_network)
        self.assertIn('[W]', text)
        self.assertIn('192.168.0.2', text)
        install.ARGS.host = '127.0.0.1'
        self.assertTrue(self.quiet(install.step_network)[0])

    def test_the_same_rpi_at_a_new_address_keeps_its_token(self):
        sim = SimServer(token='0123456789abcdef')
        self.addCleanup(sim.close)
        with tempfile.TemporaryDirectory() as folder:
            self.patch(install, 'PC', Path(folder))
            (Path(folder) / 'data').mkdir()
            install.ARGS = types.SimpleNamespace(tcp_port=sim.port, reinstall=False, yes=True)
            install.save_config('192.168.0.82', '0123456789abcdef')  # where the RPi used to be
            token, text = self.quiet(install.step_rpi, '127.0.0.1')
            self.assertEqual(token, '0123456789abcdef')
            self.assertEqual(install.saved_config(), {'rpi_host': '127.0.0.1', 'rpi_port': sim.port,
                                                      'token': '0123456789abcdef'})
            self.assertIn('192.168.0.82', text)
            self.assertNotIn('password', text)  # nothing was installed, so nothing was asked


class InstallerHelperTests(unittest.TestCase):
    def test_suggested_computer_address(self):
        self.assertEqual(install.suggested_address('192.168.1.1'), '192.168.1.2')
        self.assertEqual(install.suggested_address('10.0.5.2'), '10.0.5.3')

    def test_package_version_matches_the_server_source(self):
        self.assertEqual(install.package_server_version(), tcpd.VERSION)

    def test_staged_package_has_unix_scripts_and_the_token(self):
        with tempfile.TemporaryDirectory() as folder:
            simulated = Path(folder) / 'rpi'
            (simulated / 'tcpd').mkdir(parents=True)
            (simulated / '__pycache__').mkdir()
            (simulated / '__pycache__' / 'x.pyc').write_bytes(b'cache')
            (simulated / 'install_rpi.sh').write_bytes(b'#!/bin/bash\r\necho hi\r\n')  # as if a copy tool broke it
            (simulated / 'tcpd' / 'tool.py').write_bytes(b'print(1)\r\n')
            original = install.RPI
            install.RPI = simulated
            self.addCleanup(setattr, install, 'RPI', original)
            staged = install.stage_package('0123456789abcdef', 'plasmic_ec_rpi_20260101_000000')
            self.addCleanup(lambda: __import__('shutil').rmtree(staged.parent, ignore_errors=True))
            self.assertEqual(staged.name, 'plasmic_ec_rpi_20260101_000000')
            self.assertEqual((staged / 'install_rpi.sh').read_bytes(), b'#!/bin/bash\necho hi\n')
            self.assertEqual((staged / 'tcpd' / 'tool.py').read_bytes(), b'print(1)\r\n')  # only scripts are touched
            self.assertEqual((staged / 'tcpd' / 'token.txt').read_bytes(), b'0123456789abcdef\n')
            self.assertFalse((staged / '__pycache__').exists())
            self.assertEqual((simulated / 'install_rpi.sh').read_bytes(), b'#!/bin/bash\r\necho hi\r\n')  # source untouched

    def test_connection_settings_are_written_where_the_program_reads_them(self):
        with tempfile.TemporaryDirectory() as folder:
            original, install.PC = install.PC, Path(folder)
            self.addCleanup(setattr, install, 'PC', original)
            install.ARGS = types.SimpleNamespace(tcp_port=8767)
            (Path(folder) / 'data').mkdir()
            self.assertEqual(install.saved_config()['token'], '')
            install.save_config('192.168.1.1', '0123456789abcdef')
            self.assertEqual(install.saved_config(), {'rpi_host': '192.168.1.1', 'rpi_port': 8767,
                                                      'token': '0123456789abcdef'})

    def test_a_computer_with_a_key_is_recognised_without_typing_anything(self):
        """key_login asks ssh in batch mode (it can never stop at a password prompt) and believes only exit 0."""
        install.ARGS = types.SimpleNamespace(identity='', known_hosts='', ssh_port=22, user='wow')
        seen = []

        def run(args, **options):
            seen.append((args, options))
            return types.SimpleNamespace(returncode=outcome)
        original = install.subprocess.run
        install.subprocess.run = run
        self.addCleanup(setattr, install.subprocess, 'run', original)
        outcome = 0
        self.assertTrue(install.key_login('192.0.2.7'))
        args, options = seen[0]
        self.assertEqual((args[0], args[-2:]), ('ssh', ['wow@192.0.2.7', 'true']))
        self.assertIn('BatchMode=yes', args)
        self.assertTrue(options['capture_output'] and options['timeout'] <= 30)
        outcome = 255  # ssh: Permission denied (publickey,password)
        self.assertFalse(install.key_login('192.0.2.7'))

        def missing(args, **options):
            raise FileNotFoundError('ssh')
        install.subprocess.run = missing
        self.assertFalse(install.key_login('192.0.2.7'))

    def test_ssh_options(self):
        install.ARGS = types.SimpleNamespace(identity='', known_hosts='')
        self.assertEqual(install.ssh_options(), ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10'])
        install.ARGS = types.SimpleNamespace(identity='k', known_hosts='h')
        self.assertEqual(install.ssh_options()[4:], ['-i', 'k', '-o', 'BatchMode=yes', '-o', 'UserKnownHostsFile=h'])


def config_snippet():
    """The Python embedded in install_rpi.sh that creates or updates the server's config.json."""
    script = (ROOT / 'rpi' / 'install_rpi.sh').read_text(encoding='utf-8')
    found = re.search(r"<<'PY'\n(.*?)\nPY\n", script, re.DOTALL)
    assert found, 'heredoc not found in install_rpi.sh'
    return found.group(1)


class RpiConfigSnippetTests(unittest.TestCase):
    def run_snippet(self, folder, token=None, allow=()):
        target = Path(folder) / 'config.json'
        token_file = Path(folder) / 'token.txt'
        if token is not None:
            token_file.write_text(token + '\n', encoding='ascii')
        # On the RPi the snippet prints in UTF-8. Windows would use its own code page for a pipe, and the
        # test would then pass or fail depending on whether the window it runs in sets this variable.
        done = subprocess.run([sys.executable, '-', str(ROOT / 'rpi' / 'tcpd' / 'config.example.json'), str(target),
                               '/home/pi/Desktop/Plasmic', str(token_file), *allow],
                              input=config_snippet().encode('utf-8'), capture_output=True,
                              env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        return done, (json.loads(target.read_text(encoding='utf-8')) if target.exists() else None)

    def test_first_install_without_the_pc_installer_makes_a_random_token(self):
        with tempfile.TemporaryDirectory() as folder:
            done, config = self.run_snippet(folder)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertRegex(config['token'], r'^[0-9a-f]{16}$')
            self.assertEqual(config['tools_dir'], '/home/pi/Desktop/Plasmic/nano_lcd_v1_1/rpi_tools')
            self.assertEqual(config['allow'], ['127.0.0.1', '192.168.1.2'])
            tcpd.load_config(str(Path(folder) / 'config.json'))  # the server accepts what was written

    def test_pc_installer_token_and_address_are_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            done, config = self.run_snippet(folder, token='0123456789abcdef', allow=['192.168.1.50', '192.168.1.2'])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(config['token'], '0123456789abcdef')
            self.assertEqual(config['allow'], ['127.0.0.1', '192.168.1.2', '192.168.1.50'])

    def test_existing_settings_survive_a_reinstall(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = dict(tcpd.DEFAULTS, token='aaaaaaaaaaaaaaaa', watchdog_s=8, allow=['127.0.0.1', '192.168.1.9'])
            (Path(folder) / 'config.json').write_text(json.dumps(existing), encoding='utf-8')
            done, config = self.run_snippet(folder)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(config, existing)
            self.assertIn('No change', done.stdout.decode('utf-8'))
            done, config = self.run_snippet(folder, token='bbbbbbbbbbbbbbbb', allow=['192.168.1.2'])
            self.assertEqual((config['token'], config['watchdog_s'], config['allow']),
                             ('bbbbbbbbbbbbbbbb', 8, ['127.0.0.1', '192.168.1.9', '192.168.1.2']))

    def test_a_bad_token_file_stops_the_install(self):
        with tempfile.TemporaryDirectory() as folder:
            done, config = self.run_snippet(folder, token='not a token; rm -rf /')
            self.assertNotEqual(done.returncode, 0)
            self.assertIsNone(config)


class PackagedFileTests(unittest.TestCase):
    def test_shell_scripts_use_unix_line_endings(self):
        for name in ('install_rpi.sh', 'remote_install.sh'):
            data = (ROOT / 'rpi' / name).read_bytes()
            self.assertTrue(data.startswith(b'#!/bin/bash\n'), name)
            self.assertNotIn(b'\r', data, name)
        unit = (ROOT / 'rpi' / 'system' / 'plasmic-tcpd.service').read_bytes()
        self.assertNotIn(b'\r', unit)
        self.assertIn(b'@BASE@/tcpd/plasmic_tcpd.py', unit)

    def test_remote_installer_uses_the_options_the_installer_understands(self):
        remote = (ROOT / 'rpi' / 'remote_install.sh').read_text(encoding='utf-8')
        main = (ROOT / 'rpi' / 'install_rpi.sh').read_text(encoding='utf-8')
        for option in ('--tcpd-only', '--allow', '--user'):
            self.assertIn(option, remote)
            self.assertIn(option + ')', main)
        self.assertIn('install_rpi.sh', remote)
        installer = (ROOT / 'installer' / 'install.py').read_text(encoding='utf-8')
        self.assertIn('remote_install.sh', installer)
        # bash executes while it reads: both scripts are syntax-checked before either one runs.
        self.assertIn("f'bash -n {name}/remote_install.sh && bash {name}/remote_install.sh'", installer)
        self.assertLess(remote.index('bash -n "$HERE/install_rpi.sh"'), remote.index('sudo bash "$HERE/install_rpi.sh"'))

    def test_windows_scripts_are_plain_ascii(self):
        """.cmd and .ps1 files are read in the console's code page; keep them ASCII so they run anywhere."""
        files = (list(ROOT.glob('*.cmd')) + list((ROOT / 'installer').glob('*.cmd')) + list((ROOT / 'installer').glob('*.ps1'))
                 + list((ROOT / 'mega').glob('*.cmd')))
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            try:
                path.read_bytes().decode('ascii')
            except UnicodeDecodeError:
                self.fail(f'{path.name} contains non-ASCII characters')

    def test_everything_the_launchers_need_is_present(self):
        for name in ('Install.cmd', 'Start.cmd', 'Start-Demo.cmd', 'Start-Kiosk.cmd', 'Set-PIN.cmd',
                     'guide.html', 'docs/detailed-guide.html', 'installer/install.py', 'installer/unpack-python.cmd',
                     'pc/app.py', 'pc/netcheck.py', 'pc/plasmic_client.py', 'pc/schedule.py', 'pc/web/index.html',
                     'pc/runtime/python-3.12.10-embed-amd64.zip', 'rpi/tcpd/plasmic_tcpd.py',
                     'rpi/tcpd/read_status.py', 'rpi/tcpd/plasma_measure.py', 'rpi/rpi_tools/plasma_job.py'):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_the_installer_knows_every_part_of_the_pc_program(self):
        """2.0.0 is many files. Each part is named in the installer's list, every named file exists, and
        every program the PC side is made of is covered (a new module must be added to the list)."""
        listed = set(install.PC_FILES)
        for name in listed:
            self.assertTrue((ROOT / 'pc' / name).is_file(), name)
        modules = {path.name for path in (ROOT / 'pc').glob('*.py')}
        self.assertEqual(modules - listed, set())
        self.assertFalse((ROOT / 'pc' / 'web' / 'app.js').exists())       # the 1.4.1 page is gone
        self.assertFalse((ROOT / 'pc' / 'app2.py').exists())               # and so is the name used while 2.0.0 was built
        for needed in ('application.py', 'pumpboard.py', 'analyst.py', 'analysis_default.json', 'recipes/plasma-only.csv',
                       'vendor/MANIFEST.sha256', 'vendor/site/serial/__init__.py', 'web/js/main.js'):
            self.assertIn(needed, listed)

    def test_a_folder_copied_only_in_part_is_noticed(self):
        original = install.PC_FILES
        self.addCleanup(setattr, install, 'PC_FILES', original)
        install.PC_FILES = original + ('no-such-part.py',)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(install.step_computer())
        self.assertIn('The folder is incomplete and missing:', out.getvalue())
        self.assertIn('no-such-part.py', out.getvalue())

    def test_the_launchers_are_the_ones_that_were_checked(self):
        """Every launcher is byte for byte what 1.4.1 shipped, except the two remark lines of Set-PIN.cmd."""
        known = {'Start.cmd': '3e4e04ea0ab77fb454f959fc95dfd985995ea540d1c9cb6716694eab3ca97494',
                 'Start-Demo.cmd': '9f8b83bb5ffbc4dbf8bdcb41dad1081840b786f58b2a6c2ed8555642d2a506a7',
                 'Start-Kiosk.cmd': '5440bd4211077e28022c14dddafceec7eb741e87d6e42afb0772eb61061d945b',
                 'Install.cmd': '5db1785456db7d62834b51ab333ab190c03b0bb4a11ecf56378258e3ea3f4d0d',
                 'Flash-Nano.cmd': '4f47186a68d158dfd0bf81ae1986f273763a70d4b8504604747ae82e6fbddd6e',
                 'installer/unpack-python.cmd': '91c11ed11eef5cf1ea5de140eab698e30f05b16738598647e87664152dfdd5c2'}
        for name, digest in known.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)
        pin = (ROOT / 'Set-PIN.cmd').read_bytes()
        self.assertNotIn(b'\n', pin.replace(b'\r\n', b''))            # CRLF throughout
        lines = pin.decode('ascii').split('\r\n')
        self.assertEqual([line for line in lines if line.startswith('rem ')],
                         ['rem Sets the 6-8 digit maintenance PIN. The maintenance pages stay closed until a PIN is set.',
                          'rem Run this again to replace a forgotten PIN.'])
        self.assertEqual([line for line in lines if line and not line.startswith('rem ')],
                         ['@echo off', 'call "%~dp0installer\\unpack-python.cmd"', 'if errorlevel 1 exit /b 1',
                          'cd /d "%~dp0pc"', '"runtime\\python\\python.exe" app.py --set-pin', 'pause'])

    def test_the_installer_says_what_needs_no_installing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install.about_the_pump_board_and_the_pin()
        self.assertIn('pump control board (Arduino Mega) No need to install: USB cable into this computer. After opening the program, it will connect by itself without selecting a port.', out.getvalue())
        self.assertIn('maintenance page must have PIN can enter: If it has not been set yet, double-click Set-PIN.cmd.', out.getvalue())

    def test_every_program_of_the_tcp_server_is_installed_on_the_rpi(self):
        """A program added to rpi/tcpd but not to install_rpi.sh would be missing on the RPi."""
        main = (ROOT / 'rpi' / 'install_rpi.sh').read_text(encoding='utf-8')
        programs = sorted(path.name for path in (ROOT / 'rpi' / 'tcpd').glob('*.py'))
        self.assertEqual(programs, ['plasma_measure.py', 'plasmic_tcpd.py', 'read_status.py'])
        for name in programs:
            self.assertIn(f'install_file "$PKG/tcpd/{name}" "$TCPD_DIR/{name}" 644', main)
        # the measuring program is built from the verified tools; the installer never replaces those for it
        measure = (ROOT / 'rpi' / 'tcpd' / 'plasma_measure.py').read_text(encoding='utf-8')
        self.assertIn('import plasma_job', measure)
        self.assertNotIn(b'\r', (ROOT / 'rpi' / 'tcpd' / 'plasma_measure.py').read_bytes())
        self.assertIn('plasma_quick.py', main)  # the program 1.3.0 installed is moved away, with a backup

    def test_the_server_only_update_stops_when_the_tools_on_the_rpi_are_not_the_ones_of_this_package(self):
        """The measuring program uses functions and file conventions of the four verified tools. On top
        of other versions of them it could fail in the middle of a measurement, so the update that
        leaves the tools alone (--tcpd-only) installs nothing then and says what to do instead."""
        main = (ROOT / 'rpi' / 'install_rpi.sh').read_text(encoding='utf-8')
        only = main[main.index('only for additional installation TCP server (--tcpd-only) '):main.index('say "10/11 installation TCP server')]
        self.assertIn('for f in plasma_job.py nano_verify.py pulse_prepare.py hardware_verify.py; do', only)
        self.assertIn('if ! cmp -s "$PKG/rpi_tools/$f" "$TOOLS_DIR/$f"; then', only)
        check = only[only.index('if ! cmp -s "$PKG/rpi_tools/$f"'):]
        self.assertIn('Nothing is installed', check)
        self.assertIn('exit 3', check)
        self.assertLess(check.index('Nothing is installed'), check.index('exit 3'))
        self.assertIn('First use the physical switch to turn it off 24 V drive power supply and high voltage', check)   # a full install restarts the RPi: the Nano's D13 flickers
        # nothing of the server is installed before that check
        self.assertLess(main.index('exit 3'), main.index('install_file "$PKG/tcpd/plasmic_tcpd.py"'))
        self.assertNotIn('generally can still be used', main)                          # the old warning that let it go on
        for name in ('plasma_job.py', 'nano_verify.py', 'pulse_prepare.py', 'hardware_verify.py'):
            self.assertTrue((ROOT / 'rpi' / 'rpi_tools' / name).is_file(), name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
