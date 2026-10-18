#!/usr/bin/env python3
"""Plasmic electronic control - one-click installation (by Install.cmd starts).

First confirm which machine you want to install to RPi (IP and login name are recorded in installer\\site.json; have no idea IP can be searched automatically),
Then do five things in order. Each step will explain the result and what to do if it fails:
  1. Check the programs on this computer Failed to start measurement
  2. Create desktop shortcut
  3. Check this computer with RPi network between
  4. Bundle TCP Server installed RPi (use Windows built-in scp/ssh,needs to be entered on the way RPi password) 
  5. Automatically fill in the connection settings and confirm the actual connection once

RPi The password is given by Windows of ssh program reads it directly. This program cannot see or save it.
No way HSPY/Nano sends any output or high voltage command; Section 5 step only ”reads” the device status.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PC = ROOT / 'pc'
RPI = ROOT / 'rpi'
sys.path.insert(0, str(PC))
sys.dont_write_bytecode = True  # no __pycache__ beside the programs

import app as pc_app  # noqa: E402
import netcheck  # noqa: E402
from plasmic_client import Client, PlasmicError  # noqa: E402

TOTAL = 5
# What the PC program of 2.0.0 cannot start without (one file of each part; a folder that was copied
# only in part is noticed here, not at the first click).
PC_FILES = ('app.py', 'application.py', 'httpd.py', 'store.py', 'migrate.py', 'pinlock.py', 'alarms.py', 'rpilink.py',
            'schedule.py', 'recipe.py', 'pumpboard.py', 'pump_guard.py', 'devices.py', 'steps.py', 'runner.py', 'manual.py',
            'analysis.py', 'analysis_default.json', 'calibration.py', 'measurements.py', 'analyst.py',
            'megasim.py', 'netcheck.py', 'plasmic_client.py', 'recipes/plasma-only.csv',
            'vendor/MANIFEST.sha256', 'vendor/v7/backend/transport.py', 'vendor/v7/config/hardware.yaml',
            'vendor/site/serial/__init__.py', 'vendor/site/yaml/__init__.py',
            'web/index.html', 'web/js/main.js', 'web/css/base.css', 'web/fonts/barlow-400.ttf')
ARGS = None
# Which RPi this folder installs to. Used when Install.cmd is started without options.
SITE = HERE / 'site.json'
BUILT_IN_SITE = {'rpi_host': '192.168.1.1', 'rpi_user': 'pi'}
USER_NAME = re.compile(r'[a-z_][a-z0-9_-]{0,31}')


# ---------------------------------------------------------------- Output and Question

def say(text=''):
    print(text, flush=True)


def heading(number, text):
    say()
    say(f'[{number}/{TOTAL}] {text}')
    say('─' * 62)


def ok(text):
    say('  [ OK ] ' + text)


def note(text):
    say('  [Attention] ' + text)


def fail(text):
    say('  [failed] ' + text)


def ask(prompt, default=''):
    """One line from the person; with --yes (unattended) the default is used."""
    if ARGS.yes:
        return default
    try:
        answer = input(prompt).strip().lstrip('')  # a byte-order mark when answers are piped in
    except EOFError:
        return default
    return answer or default


def ask_yes(prompt, default):
    answer = ask(f'{prompt} [{"Y/n" if default else "y/N"}] ', 'y' if default else 'n').lower()
    return answer.startswith('y')


def choose(prompt, options, default):
    """options: {'r': 'try again', ...}; returns the chosen key."""
    say()
    for key, label in options.items():
        say(f'    [{key.upper()}] {label}')
    while True:
        answer = ask(f'  {prompt} (Press directly Enter={default.upper()}) : ', default).lower()[:1]
        if answer in options:
            return answer


def powershell(script, *arguments, capture=True):
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HERE / script), *arguments]
    return subprocess.run(command, capture_output=capture, timeout=120)


# ---------------------------------------------------------------- be installed on? Path in RPi

def valid_ip(text):
    try:
        return bool(netcheck.IPV4.fullmatch(text)) and ipaddress.IPv4Address(text) is not None
    except ValueError:
        return False


def load_site():
    """The RPi's IP and login name from site.json; anything missing or malformed falls back."""
    site = dict(BUILT_IN_SITE)
    try:
        raw = json.loads(SITE.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return site
    if isinstance(raw, dict):
        host, user = raw.get('rpi_host'), raw.get('rpi_user')
        if isinstance(host, str) and valid_ip(host.strip()):
            site['rpi_host'] = host.strip()
        if isinstance(user, str) and USER_NAME.fullmatch(user.strip()):
            site['rpi_user'] = user.strip()
    return site


def save_site():
    """Remember what the person chose. The password is never asked for or stored here."""
    try:
        pc_app.write_json(SITE, {'rpi_host': ARGS.host, 'rpi_user': ARGS.user})
    except OSError as exc:
        note(f'There is no way to RPi is recorded The packaging layer of {SITE.name} ({exc}) ; Please enter it again next time.')


def change_target():
    while True:
        answer = ask(f'  RPi of IP address (Press directly Enter={ARGS.host}) : ', ARGS.host)
        if valid_ip(answer):
            break
        say('  format is wrong.IP The address is four sets of numbers,For example 192.168.0.82')
    ARGS.host = answer
    while True:
        answer = ask(f'  RPi login name (Press directly Enter={ARGS.user}) : ', ARGS.user)
        if USER_NAME.fullmatch(answer):
            break
        say('  format is wrong. The login name is lowercase English and numbers, for example pi')
    ARGS.user = answer
    save_site()


def find_rpi():
    """Search this computer's networks; the person picks from what answered. True when changed."""
    say()
    say('  automatic search RPi (about 5–15 seconds)…')
    say('    ① broadcasts on the regional network asking: has it been installed? Plasmic TCP Server’s RPi?')
    say(f'    ② Try connecting once to each address in the network segment where this computer is located. SSH (port {ARGS.ssh_port}) ,Check which devices can log in.')
    say('       only in 192.168.x.x This is done on an internal network; it just ”knocks on the door” and will not log in to any device.')
    rows, seen = [], set()
    try:
        servers = netcheck.discover(ARGS.tcp_port, timeout=1.5)
        machines = netcheck.find_ssh_hosts(ARGS.ssh_port)
    except (OSError, ValueError) as exc:
        fail(f'Error while searching:{exc}')
        return False
    for row in servers:
        if not row['sim']:
            seen.add(row['host'])
            rows.append((row['host'], f'has been installed Plasmic TCP server {row["version"]}'
                         + (f' (Name {row["name"]}) ' if row['name'] else '')))
    for row in machines:
        if row['host'] not in seen:
            detail = ', '.join(part for part in (row['banner'], row['mac'] and 'network card ' + row['mac']) if part)
            rows.append((row['host'], ('It seems Raspberry Pi' if row['rpi'] else 'can be used SSH Login, but not sure if it is The content of RPi')
                         + (f' ({detail}) ' if detail else '')))
    if not rows:
        fail('No device can be found to log in to.')
        say('         Common causes: RPi is not powered on, or is still on (approx. 1 minutes);RPi and this computer are not connected to the same network;')
        say('         RPi is not enabled SSH.You can also connect the screen and keyboard RPi,Open terminal input hostname -I look at it IP.')
        return False
    say()
    for index, (host, text) in enumerate(rows, 1):
        say(f'    [{index}] {host:<15}  {text}')
    answer = ask('  ? Enter the number (press directly Enter=is neither):', '')
    if not answer.isdigit() or not 1 <= int(answer) <= len(rows):
        return False
    ARGS.host = rows[int(answer) - 1][0]
    save_site()
    ok(f'RPi of IP address changed to {ARGS.host} (has been remembered, no need to look for it next time)')
    return True


def ask_target():
    """Show which RPi will be installed to; the person may change it or search for it."""
    while True:
        say()
        say('  is to be installed on this machine RPi: ')
        say(f'      IP address : {ARGS.host}')
        say(f'      Login name:{ARGS.user}')
        say('   (The password will not be stored; you can enter it later in this window. )')
        choice = choose('Please select', {'y': 'This is it, start the installation',
                                  'c': 'Revise IP address or login name',
                                  'f': 'have no idea RPi of IP: automatic search'}, 'y')
        if choice == 'y':
            return
        if choice == 'c':
            change_target()
        else:
            find_rpi()


# ---------------------------------------------------------------- 1 PC version

def step_computer():
    heading(1, 'Check the programs on this computer Failed to start measurement')
    ok(f'enclosed Python {sys.version.split()[0]} can be used (no additional installation required Python) ')
    missing = [str(path.relative_to(ROOT)) for path in (
        *(PC / name for name in PC_FILES), RPI / 'install_rpi.sh',
        RPI / 'remote_install.sh', RPI / 'tcpd' / 'plasmic_tcpd.py', RPI / 'tcpd' / 'plasma_measure.py',
        RPI / 'rpi_tools' / 'plasma_job.py')
               if not path.is_file()]
    if missing:
        fail('The folder is incomplete and missing:' + ', '.join(missing))
        say('         Please copy the entire folder again (not just a few files).')
        return False
    (PC / 'data').mkdir(exist_ok=True)
    ok(f'program file complete (version {pc_app.VERSION}) ,folder:{ROOT}')
    return True


# ---------------------------------------------------------------- 2 Shortcut

def step_shortcuts():
    heading(2, 'Create desktop shortcut')
    if ARGS.no_shortcuts:
        note('has been skipped (--no-shortcuts) ')
        about_the_pump_board_and_the_pin()
        return
    made = []
    for name, target, text in (('Plasmic Electronic control', 'Start.cmd', 'Open the operation screen and connect to RPi'),
                               ('Plasmic electronic control (practice mode)', 'Start-Demo.cmd', 'Equipment-free practice mode')):
        arguments = ['-Name', name, '-Target', str(ROOT / target), '-WorkDir', str(ROOT), '-Description', text]
        if ARGS.shortcut_dir:
            arguments += ['-Dir', ARGS.shortcut_dir]
        try:
            done = powershell('shortcut.ps1', *arguments)
        except (OSError, subprocess.TimeoutExpired) as exc:
            done = None
            reason = str(exc)
        if done is not None and done.returncode == 0:
            made.append(name)
        else:
            reason = reason if done is None else done.stderr.decode('utf-8', 'replace').strip()[-200:]
            note(f'shortcut“{name}”was not established successfully ({reason}) ; does not affect use, just double-click in the folder {target} is enough.')
    if made:
        ok('There are more on the desktop:' + ', '.join(f'“{name}”' for name in made))
    about_the_pump_board_and_the_pin()


def about_the_pump_board_and_the_pin():
    """Two things a person looks for in an installer and that need none (2.0.0)."""
    note('pump control board (Arduino Mega) No need to install: USB cable into this computer. After opening the program, it will connect by itself without selecting a port.')
    note('maintenance page must have PIN can enter: If it has not been set yet, double-click Set-PIN.cmd.')


# ---------------------------------------------------------------- 3 Network

def computer_address(host):
    return next((address for address in netcheck.local_ipv4() if netcheck.same_subnet(address, host)), '')


def suggested_address(host):
    prefix, last = host.rsplit('.', 1)
    return f'{prefix}.{3 if last == "2" else 2}'


def list_adapters():
    try:
        done = powershell('adapters.ps1')
        return json.loads(done.stdout.decode('utf-8-sig')) if done.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def set_address(host):
    """Let the person pick a wired adapter; Windows then asks for administrator approval."""
    adapters = list_adapters()
    if not adapters:
        note('The wired network card cannot be found, or the network card list cannot be read. Please use ”Customize” instead.')
        return
    address = suggested_address(host)
    say()
    say('  This computer’s wired network card:')
    for index, adapter in enumerate(adapters, 1):
        state = 'line has been connected' if adapter['connected'] else 'is not connected (or the other party is not turned on)'
        current = ', '.join(adapter['addresses']) or 'None IP'
        internet = ' ← This one is currently used for surfing the Internet, do not select it.' if adapter['gateway'] else ''
        say(f'    [{index}] {adapter["name"]} ({adapter["description"]}) ｜{state}｜at present {current}{internet}')
    say('  hint: Take over RPi and plug it back in. Run this program again and see which one’s status changes accordingly. That’s the one.')
    answer = ask(f'  Which one should be set to {address}/255.255.255.0?Enter the number (press directly Enter=Not set) : ', '')
    if not answer.isdigit() or not 1 <= int(answer) <= len(adapters):
        return
    adapter = adapters[int(answer) - 1]
    if adapter['gateway'] and not ask_yes(f'  “{adapter["name"]}”is currently able to access the Internet, change it to fixed IP , this network card cannot access the Internet. Are you sure?', False):
        return
    say(f'  is about to be executed (Windows A window of ”Do you want to allow changes?” will pop up, please click ”Yes”): The response from the')
    say(f'    netsh interface ipv4 set address name="{adapter["name"]}" static {address} 255.255.255.0')
    try:
        done = powershell('set-ip.ps1', '-Name', adapter['name'], '-Ip', address, capture=False)
        code = done.returncode
    except (OSError, subprocess.TimeoutExpired):
        code = 1
    if code == 0:
        time.sleep(4)
    else:
        note('was not set successfully (maybe you clicked ”No” in the permission window).')


def reachable(host):
    """True when the RPi shows any sign of life: a ping reply, the TCP server, or SSH."""
    return (netcheck.ping(host) or netcheck.probe(host, ARGS.tcp_port)['tcp']
            or netcheck.port_open(host, ARGS.ssh_port))


def step_network():
    """Returns True when the RPi (ARGS.host, which the person may change here) answers."""
    heading(3, f'Check this computer with RPi ({ARGS.host}) network between')
    while True:
        host = ARGS.host
        if host.startswith('127.'):
            ok('The target is the computer itself (emulator), no network line is required')
            return True
        mine = computer_address(host)
        if reachable(host):
            if mine:
                ok(f'This computer’s IP yes {mine},and RPi is in the same network segment')
            else:
                note('This computer and RPi of IP is not in the same network segment (through the router); you can continue if you can connect.')
            ok(f'RPi ({host}) has a response')
            return True
        options = {'r': 'I checked, check again',
                   'f': 'automatic search RPi (RPi of IP may be different from the above)',
                   'c': 'Enter it yourself RPi of IP address or login name'}
        if mine:
            fail(f'{host} No response. (This computer’s IP yes {mine},network segment is correct. )')
            say('         Please check:RPi powered on (it takes approximately 1 minutes); whether both ends of the network cable are plugged in properly;')
            say(f'         RPi of IP Is it true? {host}——is connected to the router,RPi of IP May be replaced,select F can find it.')
        else:
            current = ', '.join(netcheck.local_ipv4()) or ' (does not have any network address)'
            fail(f'{host} No response, and this computer does not {host.rsplit(".", 1)[0]}.x of IP.current IP: {current}')
            say('         Three possibilities: ')
            say(f'           a. RPi of IP Actually not {host} → select F automatically search, or select C Enter it yourself; ')
            say('           b. Computer and  RPi is connected directly with a network cable, but the network card of the computer has not been set and fixed. IP → select W or A; ')
            say('           c. has been set, but the network cable is not connected properly or RPi is not turned on (at this time Windows That address will not be enabled) .')
            options['w'] = (f'turn on Windows ’s ”Network Connection” window, I set the computer’s IP'
                            f' (IP {suggested_address(host)}, mask 255.255.255.0,gateway and DNS Leave blank) ')
            options['a'] = 'Let the installer help me configure the computer The spectrum of IP (will list the network card for you to choose, and the system administrator’s consent is required)'
        options['s'] = 'Skip first RPi,Only install the computer side'
        choice = choose('Please select', options, 's' if ARGS.yes else 'r')
        if choice == 's':
            return False
        if choice == 'f':
            find_rpi()
        elif choice == 'c':
            change_target()
        elif choice == 'w':
            subprocess.run(['cmd', '/c', 'start', '', 'ncpa.cpl'])
            ask('  After setting, press Enter continue…')
        elif choice == 'a':
            set_address(host)
        say()
        say(f'  Recheck {ARGS.host} …')


# ---------------------------------------------------------------- 4 RPi

def package_server_version():
    text = (RPI / 'tcpd' / 'plasmic_tcpd.py').read_text(encoding='utf-8')
    found = re.search(r"^VERSION = '([^']+)'", text, re.MULTILINE)
    return found.group(1) if found else '?'


def saved_config():
    try:
        return pc_app.validate_config(json.loads((PC / 'data' / 'config.json').read_text(encoding='utf-8-sig')))
    except (OSError, ValueError):
        return dict(pc_app.DEFAULT_CONFIG)


def save_config(host, token):
    config = pc_app.validate_config({'rpi_host': host, 'rpi_port': ARGS.tcp_port, 'token': token})
    pc_app.write_json(PC / 'data' / 'config.json', config)


def ssh_options():
    options = ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10']
    if ARGS.identity:
        options += ['-i', ARGS.identity, '-o', 'BatchMode=yes']
    if ARGS.known_hosts:
        options += ['-o', f'UserKnownHostsFile={ARGS.known_hosts}']
    return options


def stage_package(token, name):
    """A temporary copy of the rpi folder plus the token for this computer; returns its path."""
    staging = Path(tempfile.mkdtemp(prefix='plasmic_install_')) / name
    shutil.copytree(RPI, staging, ignore=shutil.ignore_patterns('__pycache__', 'sim-data', 'token.txt'))
    for script in staging.rglob('*.sh'):
        data = script.read_bytes()
        if b'\r' in data:  # a copy tool turned the line endings into Windows ones; bash needs Unix ones
            script.write_bytes(data.replace(b'\r\n', b'\n').replace(b'\r', b'\n'))
    (staging / 'tcpd' / 'token.txt').write_bytes(token.encode('ascii') + b'\n')
    return staging


def key_login(host):
    """True when ssh gets into the RPi without asking anything: this computer has a key set up for it."""
    try:
        done = subprocess.run(['ssh', '-p', str(ARGS.ssh_port), *ssh_options(), '-o', 'BatchMode=yes',
                               f'{ARGS.user}@{host}', 'true'], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def install_on_rpi(host):
    """Copy the package and run its installer on the RPi. Returns (token or '', ssh exit code)."""
    token = secrets.token_hex(8)
    name = 'plasmic_ec_rpi_' + datetime.now().strftime('%Y%m%d_%H%M%S')
    say()
    say(f'  Will use it next Windows built-in scp and ssh connected to {ARGS.user}@{host}.')
    if ARGS.identity:
        pass
    elif key_login(host):
        say()
        say('  ▌ This computer has been set up to log in to this computer with a key RPi: will not ask for the password, just wait for it to finish running.')
    else:
        say()
        say(f'  ▌ screen will appear twice:{ARGS.user}@{host}\'s password:')
        say('  ▌ Enter both times RPi login password, then press Enter.')
        say('  ▌ The screen will not display any characters (even ＊ None), this is normal.')
        say('  ▌  (The first time is to transfer the file, and the second time is to perform the installation. )')
        say(f'  ▌ If it appears again later [sudo] password for {ARGS.user}: ,Enter the same password again.')
        say('  ▌ If you ask first Are you sure you want to continue connecting,Ignore it, it will continue automatically.')
        say('  ▌ password is given by Windows of ssh is read directly by the program and cannot be seen by the installation program and will not be saved.')
    staging = stage_package(token, name)
    try:
        while True:
            target = f'{ARGS.user}@{host}'
            say()
            say('  ① Send the installation file to RPi …')
            code = subprocess.call(['scp', '-r', '-P', str(ARGS.ssh_port), *ssh_options(), str(staging), f'{target}:{name}'])
            if code == 0:
                ok('file has been sent RPi')
                break
            fail(f'file was not sent successfully (end code {code}) .Common causes: ')
            say('           • The password is wrong (it is normal that the characters will not be displayed when typing)→ select R Try again.')
            say(f'           • The login name is not {ARGS.user} → select U Revise.')
            say('           • appear above“REMOTE HOST IDENTIFICATION HAS CHANGED”: this IP was connected to another one before RPi,')
            say('             or this one RPi has been refilled. Make sure the person connected to the line is indeed yours RPi back,select K.')
            say('           • appear above“Connection refused”: RPi is not enabled SSH (exist RPi execute on sudo raspi-config →')
            say('             Interface Options → SSH → enabled).')
            choice = choose('Please select', {'r': 'try again', 'u': f'Modify the login name (now it is {ARGS.user}) ',
                                      'k': 'Clear this IP try again after the old record', 's': 'Skip first RPi'},
                            's' if ARGS.yes else 'r')
            if choice == 's':
                return '', 1
            if choice == 'u':
                answer = ask(f'  RPi login name (Press directly Enter={ARGS.user}) : ', ARGS.user)
                if USER_NAME.fullmatch(answer):
                    ARGS.user = answer
                    save_site()
                else:
                    say('  The format is wrong and there is no change.')
            if choice == 'k':
                entry = host if ARGS.ssh_port == 22 else f'[{host}]:{ARGS.ssh_port}'
                subprocess.call(['ssh-keygen', '-R', entry])
        say()
        say('  ② exist RPi …(below The program on RPi The output of) ')
        say('  ' + '·' * 60)
        # bash runs a script as it reads it: check the syntax first, so a broken script does nothing at all.
        code = subprocess.call(['ssh', '-t', '-p', str(ARGS.ssh_port), *ssh_options(), target,
                                f'bash -n {name}/remote_install.sh && bash {name}/remote_install.sh'])
        say('  ' + '·' * 60)
        return token, code
    finally:
        shutil.rmtree(staging.parent, ignore_errors=True)


def step_rpi(host):
    """Returns the token to use, or '' when the RPi side was skipped or failed."""
    heading(4, 'Bundle TCP Server installed RPi')
    config = saved_config()
    # The saved token also fits when only the RPi's IP changed: it is still the same RPi.
    token = config['token'] if config['rpi_port'] == ARGS.tcp_port else ''
    found = netcheck.probe(host, ARGS.tcp_port, token)
    wanted = package_server_version()
    if found['authed'] and not ARGS.reinstall:
        if config['rpi_host'] != host:
            save_config(host, token)
            ok(f'in connection settings RPi address has been changed from {config["rpi_host"]} changed to {host}')
        running = found['hello'].get('version')
        if running == wanted:
            ok(f'RPi parameters on TCP server ({running}) ,This computer’s token Can also be used')
            if not ask_yes('  To reinstall RPi end? (Generally not required)', False):
                return token
        else:
            note(f'RPi on TCP The server is {running},This installation package is {wanted}')
            if not ask_yes('  to update RPi end? (recommended update)', True):
                return token
    elif found['tcp']:
        note(f'RPi There is already TCP server, but this computer cannot connect yet ({found["error"]}) .Reinstallation will set both sides.')
    else:
        say('  RPi Not yet TCP server, install now.')
    if not shutil.which('ssh') or not shutil.which('scp'):
        fail('This computer doesn’t have ssh/scp instruction (Windows of“OpenSSH client is not installed).')
        say('         Installation method: Settings → app → optional function → New features → search“OpenSSH client”→ installation, and then execute it again Install.cmd.')
        say('         or use manual installation instead (explain ”References › Manual installation RPi end”).')
        return ''
    new_token, code = install_on_rpi(host)
    if not new_token:
        return ''
    if code == 0:
        ok('RPi reports installation completed')
        ARGS.wait = 30
    elif code == 10:
        # remote_install.sh: a first-time (full) install finished and a reboot is scheduled.
        note('RPi reports that the complete installation is complete, it will be in 1 It will automatically restart after a few minutes; it will not be connected until it restarts. Please wait.')
        ARGS.wait = 300
    else:
        fail(f'RPi was not completed (end code The program on {code}) .The original connection settings have not been changed;HSPY/Nano is not affected.')
        say('         • There is nothing on it RPi : Most likely the second password was entered incorrectly, please re-execute. The firmware of Install.cmd is enough.')
        say('         • appear above syntax error: The installation script failed the syntax check.RPi ; please note those lines.')
        say('         • Other output: Please see the last few lines of the error message (denoting ”when something went wrong”).')
        return ''
    # Only now: the RPi accepted the new token, so this computer may switch to it.
    save_config(host, new_token)
    ok('connection setting (RPi address, port, token) has been filled in automatically, no need to enter it manually.')
    return new_token


# ---------------------------------------------------------------- 5 confirm

def step_verify(host, token):
    heading(5, 'Actual confirmation once')
    deadline = time.monotonic() + ARGS.wait
    found = netcheck.probe(host, ARGS.tcp_port, token)
    waited = False
    while not found['authed'] and time.monotonic() < deadline:
        if not waited:
            say(f'  Waiting RPi Server startup on (most {ARGS.wait} seconds)', )
            waited = True
        time.sleep(3)
        print('.', end='', flush=True)
        found = netcheck.probe(host, ARGS.tcp_port, token)
    if waited:
        say()
    if not found['authed']:
        fail(f'cannot connect RPi of TCP server:{found["error"] or found["code"]}')
        say('         After opening the operation screen, click ”Connection Diagnosis” in the upper right corner. It will point out which step it is stuck on; or read the explanation ”When something goes wrong”.')
        return False
    hello = found['hello']
    ok(f'Connected {"simulator" if hello.get("sim") else "RPi"} of TCP server (Version {hello.get("version")}) ,token correct')
    try:
        with Client(host, ARGS.tcp_port, token) as client:
            status = client.request('status', fresh=True)
    except (PlasmicError, OSError, ValueError) as exc:
        note(f'Connection successful,But an error occurred while reading the device status: {exc}')
        return True
    snapshot = status.get('snapshot') or {}
    power, nano = snapshot.get('power') or {}, snapshot.get('nano') or {}
    if not snapshot:
        note('The connection is successful, but the device status cannot be read:' + str(status.get('error', '')))
        say('         Common causes: RS485 USB not plugged in,HSPY or Nano Not powered on, old HMI is still on.')
        return True
    if power.get('valid') is True:
        ok(f'HSPY power supply:{"ON" if power.get("run_stop_setting") else "OFF"},settings {power.get("set_voltage_v"):g} V,'
           f'output {power.get("voltage_v"):g} V')
    else:
        note('Can’t read HSPY power supply:' + str(power.get('error', ''))[:160])
    if nano.get('valid') is True:
        ok(f'Nano: {nano.get("state")},firmware {nano.get("firmware")},fault {nano.get("fault")}')
    else:
        note('Can’t read Nano: ' + str(nano.get('error', ''))[:160])
    problems = status.get('ready', {}).get('problems', [])
    if status.get('ready', {}).get('plasma'):
        ok('All pre-discharge inspections passed')
    for problem in problems:
        note(f'cannot be discharged yet:{problem["reason"]} → {problem["fix"]}')
    return True


# ---------------------------------------------------------------- main program

def main():
    global ARGS
    site = load_site()
    parser = argparse.ArgumentParser(description='Plasmic electronic control one-click installation')
    parser.add_argument('--host', help=f'RPi of IP (Omit=installer\\site.json inside {site["rpi_host"]}) ')
    parser.add_argument('--user', help=f'RPi ’s login name (omitted Login name of=installer\\site.json inside {site["rpi_user"]}) ')
    parser.add_argument('--tcp-port', type=int, default=8767, help='TCP Server port (default 8767) ')
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--skip-rpi', action='store_true', help='only installed on PC')
    parser.add_argument('--reinstall', action='store_true', help='RPi has been installed and reinstalled.')
    parser.add_argument('--no-shortcuts', action='store_true')
    parser.add_argument('--no-launch', action='store_true', help='Do not ask whether to start after finishing')
    parser.add_argument('--yes', action='store_true', help='No questions, all default answers (unattended)')
    parser.add_argument('--shortcut-dir', default='', help=argparse.SUPPRESS)
    parser.add_argument('--identity', default='', help=argparse.SUPPRESS)
    parser.add_argument('--known-hosts', default='', help=argparse.SUPPRESS)
    ARGS = parser.parse_args()
    ARGS.wait = 30
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    ARGS.host = ARGS.host or site['rpi_host']
    ARGS.user = ARGS.user or site['rpi_user']
    if not valid_ip(ARGS.host):
        parser.error('--host Please give the number IP,For example 192.168.0.82')
    if not USER_NAME.fullmatch(ARGS.user):
        parser.error('--user please give RPi , for example pi')

    say('═' * 62)
    say('  Plasmic electronic control - one-click installation')
    say('═' * 62)
    say('  This program will prepare the computer and TCP Server installed RPi,And automatically fill in the connection settings.')
    say('  No way HSPY/Nano sends any output or high voltage command.')
    say('  Please confirm before installation:RPi has been turned on and the network cable has been connected; old HMI Please turn off first.')
    if ARGS.skip_rpi or ARGS.yes:
        ask('  according to Enter Start (if you want to cancel, just close this window)…')
    else:
        ask_target()

    if not step_computer():
        return finish(False, False, '')
    step_shortcuts()
    token, verified = '', False
    if ARGS.skip_rpi:
        heading(3, 'Check the network')
        note('has been skipped (--skip-rpi) ')
    elif step_network():
        token = step_rpi(ARGS.host)
        if token or netcheck.probe(ARGS.host, ARGS.tcp_port, token)['authed']:
            verified = step_verify(ARGS.host, token)
    return finish(True, verified, token)


def finish(computer_ready, verified, token):
    say()
    say('═' * 62)
    if not computer_ready:
        say('  The installation is not completed. What the')
        code = 1
    elif verified:
        say('  installation completed: computer and RPi end is ready.')
        say('  Next step: Open the operation screen,According to“guide.html”and increase the voltage only after passing all the checks.')
        code = 0
    else:
        say('  The computer terminal is ready;RPi side is not finished yet.')
        say('  • If you want to familiarize yourself with the screen first: double-click Start-Demo.cmd (Practice mode, no equipment required).')
        say('  • After troubleshooting, double-click again Install.cmd is enough (can be executed repeatedly).')
        code = 0 if ARGS.skip_rpi else 1
    say('  Help file: guide.html (the everyday guide); the complete version is docs\\detailed-guide.html.')
    say('═' * 62)
    if computer_ready and not ARGS.no_launch and not ARGS.yes:
        launcher = 'Start.cmd' if verified else 'Start-Demo.cmd'
        label = 'operation screen' if verified else 'practice mode'
        if ask_yes(f'  Turn on now{label}?', True):
            subprocess.Popen(['cmd', '/c', 'start', '', str(ROOT / launcher)], cwd=str(ROOT))
    ask('  according to Enter End…')
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\n  has been cancelled.')
        raise SystemExit(1)
