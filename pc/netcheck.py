"""connection check and ”automatic search RPi”: The ”Connection Diagnostics” on the screen is shared with the one-click installer. The ”quick” sequence of

Only use Python standard function library. Only ”see” here: check this computer IP, ping, even TCP port, server version,
verify token, Find on the local network RPi.No way HSPY/Nano .
"""
from __future__ import annotations

import ipaddress
import json
import re
import select
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# 2: plasma_run no longer takes an order to choose from; the RPi measures the one way its settings
#    say (hello["flow"], one background frame). A side that still speaks 1 is told to update.
PROTOCOL = 2
DEFAULT_PORT = 8767
IPV4 = re.compile(r'\d{1,3}(?:\.\d{1,3}){3}')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The question plasmic_tcpd (1.2.0 and later) answers over UDP, on the same port number as TCP.
DISCOVER_REQUEST = b'PLASMIC-DISCOVER 1'
# First three bytes of the hardware address of network cards made by Raspberry Pi.
RPI_MAC_PREFIXES = ('28-cd-c1', '2c-cf-67', '88-a2-9e', 'b8-27-eb', 'd8-3a-dd', 'dc-a6-32', 'e4-5f-01')
ARP_ROW = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3})\s+((?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})')


def local_ipv4():
    """IPv4 addresses this computer is using right now.

    A network card whose cable is unplugged (or whose other end is switched off) has no
    active address even when a fixed IP was configured for it.
    """
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith('127.'):
                found.append(address)
    except OSError:
        pass
    return found


def same_subnet(address, host):
    """True when both are IPv4 addresses in the same 255.255.255.0 network."""
    return (bool(IPV4.fullmatch(address) and IPV4.fullmatch(host))
            and address.rsplit('.', 1)[0] == host.rsplit('.', 1)[0])


def _number(address):
    return tuple(int(part) for part in address.split('.'))


def discover(port=DEFAULT_PORT, timeout=1.2, hosts=()):
    """Ask "is a Plasmic TCP server here?" and collect who answers. Changes nothing.

    One small UDP broadcast goes out of every network card of this computer, plus one question
    straight to each address in ``hosts`` (useful when the RPi sits behind a router that does
    not pass broadcasts). Only plasmic_tcpd 1.2.0 or later answers.

    Returns [{'host', 'port', 'version', 'name', 'sim', 'allowed'}] sorted by address;
    ``allowed`` is False when the server would refuse this computer (not on its allow list).
    """
    sockets = []
    for address in local_ipv4():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((address, 0))  # otherwise Windows uses only its preferred network card
            sockets.append(sock)
            for target in ('255.255.255.255', address.rsplit('.', 1)[0] + '.255'):
                try:
                    sock.sendto(DISCOVER_REQUEST, (target, int(port)))
                except OSError:
                    pass
        except OSError:
            continue
    if hosts:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            for host in hosts:
                try:
                    sock.sendto(DISCOVER_REQUEST, (host, int(port)))
                except OSError:
                    pass
        except OSError:
            pass
    found = {}
    deadline = time.monotonic() + timeout
    try:
        while sockets:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                readable = select.select(sockets, [], [], remaining)[0]
            except (OSError, ValueError):
                break
            for sock in readable:
                try:
                    data, sender = sock.recvfrom(1024)
                    reply = json.loads(data.decode('utf-8'))
                except (OSError, ValueError):
                    continue  # Windows reports "nobody listening there" this way; not an answer
                if not isinstance(reply, dict) or reply.get('server') != 'plasmic-tcpd':
                    continue
                answer_port = reply.get('port')
                found[sender[0]] = {
                    'host': sender[0], 'port': answer_port if type(answer_port) is int else int(port),
                    'version': str(reply.get('version', ''))[:20], 'name': str(reply.get('name', ''))[:64],
                    'sim': reply.get('sim') is True, 'allowed': reply.get('allowed') is not False}
    finally:
        for sock in sockets:
            sock.close()
    return sorted(found.values(), key=lambda row: _number(row['host']))


def _knock(host, port, timeout, banner):
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if not banner:
                return host, ''
            sock.settimeout(1.0)
            try:  # an SSH server introduces itself first, e.g. "SSH-2.0-OpenSSH_8.4p1 Raspbian-5"
                return host, sock.recv(120).decode('ascii', 'replace').strip()[:80]
            except OSError:
                return host, ''
    except OSError:
        return None


def port_open(host, port, timeout=1.5):
    """True when something accepts TCP connections on that port (nothing is sent)."""
    return _knock(host, int(port), timeout, False) is not None


def sweep(port, timeout=.5, banner=False, addresses=None):
    """Try one TCP connection to ``port`` on every neighbour address (x.x.x.1–254).

    Only private networks are searched (192.168.x.x, 10.x.x.x, 172.16–31.x.x, 169.254.x.x) and
    at most three of them, so this can never turn into knocking on strangers' computers.
    Returns [{'host', 'banner'}] for the addresses that accepted, sorted by address.
    """
    prefixes = []
    for address in local_ipv4() if addresses is None else addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        prefix = address.rsplit('.', 1)[0]
        if ip.version == 4 and ip.is_private and not ip.is_loopback and prefix not in prefixes:
            prefixes.append(prefix)
    own = set(local_ipv4() if addresses is None else addresses)
    targets = [f'{prefix}.{last}' for prefix in prefixes[:3] for last in range(1, 255)
               if f'{prefix}.{last}' not in own]
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=64) as pool:
        results = list(pool.map(lambda host: _knock(host, int(port), timeout, banner), targets))
    return [{'host': host, 'banner': text}
            for host, text in sorted((row for row in results if row), key=lambda row: _number(row[0]))]


def arp_table():
    """{ip: 'aa-bb-cc-dd-ee-ff'} for the devices this computer has talked to in the last minutes."""
    try:
        done = subprocess.run(['arp', '-a'], capture_output=True, timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return {ip: mac.lower().replace(':', '-') for ip, mac in ARP_ROW.findall(done.stdout.decode('ascii', 'replace'))}


def find_servers(port=DEFAULT_PORT, deep=False, hosts=()):
    """Plasmic TCP servers on this computer's networks, for the screen's "automatic search RPi".

    First the broadcast question (see ``discover``). With ``deep`` and no answer, every
    neighbour address is tried on ``port`` and asked for its hello, which also finds a server
    whose broadcast answer was blocked on the way.
    """
    found = discover(port, hosts=hosts)
    if found or not deep:
        return found
    for row in sweep(port):
        result = probe(row['host'], port, timeout=2.0)
        hello = result['hello'] or {}
        if hello or result['code'] == 'forbidden':
            found.append({'host': row['host'], 'port': int(port), 'version': str(hello.get('version', ''))[:20],
                          'name': str(hello.get('name', ''))[:64], 'sim': hello.get('sim') is True,
                          'allowed': result['code'] != 'forbidden'})
    return found


def find_ssh_hosts(port=22):
    """Devices nearby that accept SSH logins, the ones that look like a Raspberry Pi first.

    For the installer when nobody knows the RPi's IP and no Plasmic server is on it yet.
    Returns [{'host', 'banner', 'mac', 'rpi'}].
    """
    rows = sweep(port, banner=True)
    macs = arp_table() if rows else {}
    for row in rows:
        row['mac'] = macs.get(row['host'], '')
        row['rpi'] = row['mac'][:8] in RPI_MAC_PREFIXES or 'raspbian' in row['banner'].lower()
    return sorted(rows, key=lambda row: (not row['rpi'], _number(row['host'])))


def ping(host, timeout_ms=1000):
    options = (['-n', '1', '-w', str(timeout_ms)] if sys.platform == 'win32'
               else ['-c', '1', '-W', str(max(1, timeout_ms // 1000))])
    try:
        done = subprocess.run(['ping', *options, host], capture_output=True,
                              timeout=timeout_ms / 1000 + 4, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Windows also returns 0 for "destination host unreachable"; a real reply always shows a TTL.
    return done.returncode == 0 and b'ttl=' in done.stdout.lower()


def probe(host, port, token='', timeout=3.0):
    """Ask the TCP server who it is and whether the token is accepted. Changes nothing.

    Returns {'tcp', 'hello', 'authed', 'code', 'error'}; ``code`` is '' when everything passed,
    otherwise one of: timeout, refused, unreachable, forbidden, protocol, auth_required, auth_failed.
    """
    result = {'tcp': False, 'hello': None, 'authed': False, 'code': '', 'error': ''}
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except socket.timeout:
        result.update(code='timeout', error='Connection timeout')
        return result
    except ConnectionRefusedError:
        result.update(code='refused', error='The other party refused to connect (no program is listening on that port)')
        return result
    except OSError as exc:
        result.update(code='unreachable', error=str(exc))
        return result
    result['tcp'] = True
    try:
        with sock:
            sock.settimeout(timeout)
            stream = sock.makefile('rwb')

            def ask(message):
                try:
                    stream.write((json.dumps(message) + '\n').encode('utf-8'))
                    stream.flush()
                except OSError:
                    pass  # a refusing server has already sent its reason; read it below
                line = stream.readline()
                if not line:
                    raise ConnectionError('The server closed the connection without replying.')
                return json.loads(line)

            hello = ask({'cmd': 'hello'})
            if hello.get('ok') is not True:
                result.update(code=str(hello.get('code', 'error')), error=str(hello.get('error', '')))
                return result
            if hello.get('server') != 'plasmic-tcpd' or hello.get('protocol') != PROTOCOL:
                result.update(code='protocol', error=f'server protocol version {hello.get("protocol")} with this program {PROTOCOL} different')
                return result
            result['hello'] = hello
            if hello.get('auth_required'):
                if not token:
                    result.update(code='auth_required', error='Server needs token,But this computer has not been set up yet')
                    return result
                reply = ask({'cmd': 'auth', 'token': token})
                if reply.get('ok') is not True:
                    result.update(code=str(reply.get('code', 'error')), error=str(reply.get('error', '')))
                    return result
            result['authed'] = True
    except (OSError, ValueError) as exc:
        result.update(code='protocol', error=f'That port is not Plasmic TCP server, or the connection was interrupted ({exc}) ')
    return result


def diagnose(host, port, token, device=None):
    """Step-by-step connection check for people: what was tested, the result, and how to fix it.

    ``device`` is the latest status the program already has (or None); nothing is sent to the
    equipment for this check. Every step has ok = True / False / None (None = not tested because
    an earlier step failed).
    """
    steps = []

    def add(key, title, ok, detail, fix=''):
        steps.append({'key': key, 'title': title, 'ok': ok, 'detail': detail, 'fix': fix})

    result = probe(host, port, token)
    local = host in ('127.0.0.1', 'localhost')
    addresses = local_ipv4()
    mine = [address for address in addresses if same_subnet(address, host)]
    prefix = host.rsplit('.', 1)[0] if IPV4.fullmatch(host) else ''

    if local:
        add('ip', 'This computer’s IP', True, 'targets the local machine (emulator) and does not require a network cable. If the request is not confirmed within')
    elif not prefix:
        add('ip', 'This computer’s IP', None, f'RPi address“{host}”not a number IP,Skip network segment check.')
    elif mine:
        add('ip', 'This computer’s IP', True, f'This computer is {mine[0]},and RPi ({host}) is in the same network segment.')
    else:
        listed = ', '.join(addresses) if addresses else ' (currently does not have any network address)'
        add('ip', 'This computer’s IP', False, f'This computer’s current IP: {listed}.None of them are {prefix}.x.',
            f'Two possibilities: ① RPi The address is actually not {host}——Click ”Search automatically” below RPi”.'
            f'② Computer and  RPi is connected directly with a network cable, but the computer’s network card has not been set up yet. {prefix}.2/255.255.255.0 (Description ”Steps 3 Connect to the Internet”);'
            'If it has been set: the network line is not connected properly or RPi When not powered on,Windows will not enable that address, please check the line and RPi power supply.')

    if local:
        add('ping', 'network cable and RPi power supply', True, 'This machine does not need to be checked.')
    elif result['tcp'] or ping(host):
        add('ping', 'network cable and RPi power supply', True, f'{host} has a response.')
    else:
        add('ping', 'network cable and RPi power supply', False, f'{host} No response (ping Doesn’t make sense) .',
            'Check: Whether both ends of the network cable are plugged in properly and whether they are plugged into the correct network holes;RPi powered on (it takes approximately 1 minute) .'
            'RPi is connected to the router, its IP may have been changed: click ”Search automatically” below RPi”.')

    if result['tcp']:
        add('port', f'RPi on TCP server (port {port}) ', True, f'port {port} A program is listening.')
    elif steps[-1]['ok']:
        add('port', f'RPi on TCP server (port {port}) ', False, f'RPi There is a response, but the port {port} cannot connect:{result["error"]}.',
            'represent RPi on TCP The server is not running. Easiest: close the program and double-click Install.cmd (one-click installation).'
            'or in RPi execute on sudo systemctl status plasmic-tcpd View.')
    else:
        add('port', f'RPi on TCP server (port {port}) ', None, 'Solve the previous item first.')

    hello = result['hello']
    if hello:
        mode = 'simulator' if hello.get('sim') else 'RPi server'
        add('hello', 'Server identity', True, f'{mode} plasmic-tcpd {hello.get("version")},protocol version {hello.get("protocol")}.')
    elif not result['tcp']:
        add('hello', 'Server identity', None, 'Solve the previous item first.')
    elif result['code'] == 'forbidden':
        add('hello', 'Server identity', False, result['error'],
            'This computer’s IP is different from when it was installed. Re-execute Install.cmd (will automatically add this computer to the allowed list);'
            'or refer to ”Reference Materials” › Allow List” manual modification RPi on config.json.')
    else:
        add('hello', 'Server identity', False, result['error'], 'If the versions at both ends are different, re-execute Install.cmd renew RPi end.')

    if result['authed']:
        add('token', 'token (passcode)', True, 'The server accepts this computer’s token.' if hello and hello.get('auth_required')
            else 'This server has no settings token.')
    elif not hello:
        add('token', 'token (passcode)', None, 'Solve the previous item first.')
    else:
        add('token', 'token (passcode)', False, result['error'],
            'Re-execute Install.cmd (will automatically set the token) ; or click ”Connection Settings” to paste RPi superior config.json inside token.')

    snapshot = (device or {}).get('snapshot') if result['authed'] else None
    if not result['authed']:
        add('device', 'HSPY and Nano', None, 'will be checked after the connection is successful.')
    elif not snapshot:
        add('device', 'HSPY and Nano', False if (device or {}).get('error') else None,
            (device or {}).get('error') or 'The device status has not been read yet. Please click ”Recheck” again later.',
            'Press ”Reread Status” on the main screen.' if (device or {}).get('error') else '')
    else:
        problems = []
        for key, name, hint in (('power', 'HSPY power supply', 'HSPY turned on?RS485 wiring, address is 1'),
                                ('nano', 'Nano', 'Nano powered by, RS485 wiring, address is 2, Is the firmware 1.2.1')):
            part = snapshot.get(key) or {}
            if part.get('valid') is not True:
                problems.append((f'{name} cannot be read ({str(part.get("error", ""))[:120]}) ', f'examine{hint}.'))
        if problems:
            add('device', 'HSPY and Nano', False, '; '.join(reason for reason, _ in problems),
                ' '.join(fix for _, fix in problems))
        else:
            add('device', 'HSPY and Nano', True, 'HSPY power supply and Nano can be read.')
    return steps
