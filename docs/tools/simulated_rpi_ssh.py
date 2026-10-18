"""Throwaway test double for the installer: an SSH server that looks enough like the RPi.

  * accepts any key / password,
  * stores what scp (SFTP) uploads under a temporary "home",
  * for `bash plasmic_ec_rpi_<stamp>/remote_install.sh` it checks the upload against the source
    folder byte for byte, then starts the Plasmic simulator with the uploaded token (which is
    what the real install does with the real server), and deletes the upload like the real script.

usage: simulated_rpi_ssh.py HOME SSH_PORT TCP_PORT PYTHON TCPD_PATH SOURCE_RPI_DIR REPORT_JSON [full]

Needs the third-party package `paramiko`, which is NOT in this folder's bundled Python: run this
file with a Python that has it (ask the user before downloading anything). Then run the installer
against it, for example:

    python installer\\install.py --host 127.0.0.1 --user wow --ssh-port 22022 --tcp-port 8795 --yes
        --no-launch --no-shortcuts --identity <private key> --known-hosts <scratch file>

(any key is accepted; OpenSSH refuses a key file other users can read, so restrict it with icacls).
Afterwards stop this server and the simulator it started, and delete pc\\data\\config.json — the
installer wrote 127.0.0.1 into it, which would make the user's screen connect to the wrong place.
The optional last argument `full` imitates a first-time install: exit code 10 and a late start.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import paramiko
from paramiko import SFTP_OK, SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface

HOME = Path(sys.argv[1])
SSH_PORT, TCP_PORT = int(sys.argv[2]), int(sys.argv[3])
PYTHON, TCPD, SOURCE, REPORT = sys.argv[4], sys.argv[5], Path(sys.argv[6]), Path(sys.argv[7])
HOME.mkdir(parents=True, exist_ok=True)
HOST_KEY = paramiko.RSAKey.generate(2048)
report = {'commands': [], 'auth': [], 'sim_pid': None}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(channel, command):
    def out(text):
        channel.sendall((text + '\r\n').encode('utf-8'))
    code = 1
    try:
        report['commands'].append(command)
        match = re.fullmatch(r'bash -n (plasmic_ec_rpi_\d{8}_\d{6})/remote_install\.sh && bash \1/remote_install\.sh',
                             command)
        if not match:
            out('unexpected command: ' + command)
            return
        folder = HOME / match.group(1)
        uploaded = {str(p.relative_to(folder)).replace('\\', '/'): p for p in folder.rglob('*') if p.is_file()}
        source = {str(p.relative_to(SOURCE)).replace('\\', '/'): p for p in SOURCE.rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts}
        different = sorted(name for name in source if name not in uploaded or digest(uploaded[name]) != digest(source[name]))
        extra = sorted(set(uploaded) - set(source))
        with_cr = sorted(name for name, path in uploaded.items() if name.endswith('.sh') and b'\r' in path.read_bytes())
        token = uploaded['tcpd/token.txt'].read_text(encoding='ascii').strip() if 'tcpd/token.txt' in uploaded else ''
        report.update(files=len(uploaded), different=different, extra=extra, scripts_with_cr=with_cr,
                      token_ok=bool(re.fullmatch(r'[0-9a-f]{16}', token)))
        out(f' (simulated for testing RPi) received {len(uploaded)} files; different from source:{len(different)}; extra:{extra}')
        if different or with_cr or extra != ['tcpd/token.txt'] or not report['token_ok']:
            out('The uploaded content does not meet expectations. Execute on')
            return
        def start_sim():
            sim = subprocess.Popen([PYTHON, TCPD, '--sim', '--bind', '127.0.0.1', '--port', str(TCP_PORT), '--token', token,
                                    '--sim-dir', str(HOME / 'sim')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            report['sim_pid'] = sim.pid
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.rmtree(folder)
        if len(sys.argv) > 8 and sys.argv[8] == 'full':
            # Like a first-time install: exit code 10, and the server only answers after the "reboot".
            threading.Timer(9, start_sim).start()
            out(' (simulated for testing RPi) Simulate complete installation: reply end code 10,server 9 seconds')
            code = 10
        else:
            start_sim()
            out(' (simulated for testing RPi) Already uploaded token Start the simulation server and delete the uploaded folder')
            code = 0
    except Exception as exc:  # the test must see why it failed
        report['error'] = repr(exc)
        try:
            out('error: ' + repr(exc))
        except Exception:
            pass
    finally:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        channel.send_exit_status(code)
        channel.close()


class Server(paramiko.ServerInterface):
    def get_allowed_auths(self, username):
        return 'publickey,password'

    def check_auth_publickey(self, username, key):
        report['auth'].append(f'publickey {username}')
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_password(self, username, password):
        report['auth'].append(f'password {username}')
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_env_request(self, *args):
        return True

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=run_command, args=(channel, command.decode('utf-8')), daemon=True).start()
        return True


class Handle(SFTPHandle):
    def stat(self):
        return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

    def chattr(self, attr):
        return SFTP_OK


class Files(SFTPServerInterface):
    def _real(self, path):
        return str(HOME) + self.canonicalize(path).replace('/', os.sep)

    def _try(self, action):
        try:
            return action()
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    def list_folder(self, path):
        def action():
            rows = []
            for name in os.listdir(self._real(path)):
                attr = SFTPAttributes.from_stat(os.stat(os.path.join(self._real(path), name)))
                attr.filename = name
                rows.append(attr)
            return rows
        return self._try(action)

    def stat(self, path):
        return self._try(lambda: SFTPAttributes.from_stat(os.stat(self._real(path))))

    lstat = stat

    def open(self, path, flags, attr):
        def action():
            fd = os.open(self._real(path), flags | getattr(os, 'O_BINARY', 0), 0o666)
            mode = 'ab' if flags & os.O_APPEND else 'wb' if flags & os.O_WRONLY else 'r+b' if flags & os.O_RDWR else 'rb'
            stream = os.fdopen(fd, mode)
            handle = Handle(flags)
            handle.filename = self._real(path)
            handle.readfile = handle.writefile = stream
            return handle
        return self._try(action)

    def mkdir(self, path, attr):
        return self._try(lambda: (os.mkdir(self._real(path)), SFTP_OK)[1])

    def rmdir(self, path):
        return self._try(lambda: (os.rmdir(self._real(path)), SFTP_OK)[1])

    def remove(self, path):
        return self._try(lambda: (os.remove(self._real(path)), SFTP_OK)[1])

    def rename(self, oldpath, newpath):
        return self._try(lambda: (os.replace(self._real(oldpath), self._real(newpath)), SFTP_OK)[1])

    def chattr(self, path, attr):
        return SFTP_OK


class SftpThatReportsExit(SFTPServer):
    """Real sshd reports sftp-server's exit status 0; paramiko's subsystem sends none, which
    scp treats as a failure. Send it, as the RPi's sshd would."""

    def finish_subsystem(self):
        # Before the base class closes the channel: a status sent after the close races with it.
        try:
            self.sock.send_exit_status(0)
        except Exception:
            pass
        super().finish_subsystem()


def serve(connection):
    transport = paramiko.Transport(connection)
    transport.add_server_key(HOST_KEY)
    transport.set_subsystem_handler('sftp', SftpThatReportsExit, Files)
    transport.start_server(server=Server())
    while transport.is_active():
        channel = transport.accept(1)
        if channel is None and not transport.is_active():
            break


listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', SSH_PORT))
listener.listen(5)
print('simulated RPi ssh listening on', SSH_PORT, flush=True)
while True:
    client, _ = listener.accept()
    threading.Thread(target=serve, args=(client,), daemon=True).start()
