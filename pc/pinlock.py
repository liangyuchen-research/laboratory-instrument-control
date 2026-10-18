#!/usr/bin/env python3
"""Plasmic electronic control - maintenance PIN (6–8 digits) .

pin.json format and 1.3.0 Same (PBKDF2-HMAC-SHA256, 600 000 times,32 byte salt) ,only Set-PIN.cmd will write it.
and 1.3.0 different places (SPEC B.10) : 
  * None pin.json: maintenance page is closed (1.3.0 is on). There is no need to restart the program after setting it up.
  * pin.json was replaced (reset PIN) : All logged-in screens must be logged in again.
  * peek() Only looks at how many seconds are left and will not extend; only allowed() will be extended (for maintenance operations that pass inspection).
  * Practice mode uses fixed PIN,Don’t read data folder. There is no order to choose from
PIN and its hashes will not appear in any replies, events or records.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from pathlib import Path

from store import write_json

# What --set-pin prints when it is done. (1.3.0 said "delete pin.json to remove the PIN": that would now
# close the maintenance pages.)
SAVED_TEXT = 'Maintenance PIN has been saved. forget PIN , double-click again Set-PIN.cmd Set new.'


class PinLock:
    """The maintenance PIN (6–8 digits). Without one, nothing that needs it is allowed."""
    SESSION_SECONDS = 600

    def __init__(self, path, practice_pin=None):
        if practice_pin is not None and (not isinstance(practice_pin, str) or not re.fullmatch(r'\d{6,8}', practice_pin)):
            raise ValueError('Maintenance PIN Must be 6–8 digits')
        self.practice_pin = practice_pin
        # Practice mode: the fixed PIN is all there is; no file is looked at, whatever path was given.
        self.path = None if practice_pin is not None or path is None else Path(path)
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = 0
        self.lockouts = 0
        self.next_try = 0.0
        self.seen = self._file()

    @property
    def configured(self):
        return self.practice_pin is not None or (self.path is not None and self.path.is_file())

    def _file(self):
        """What tells one pin.json from another: its times, size and place on the disk. None: no file."""
        if self.path is None:
            return None
        try:
            info = self.path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size, info.st_ctime_ns, info.st_ino

    def _follow(self):
        """Caller holds the lock. A pin.json that was replaced or removed ends every session."""
        now = self._file()
        if now != self.seen:
            self.seen = now
            self.sessions.clear()
            # Whoever can write the file could read it too: the count of wrong tries starts again.
            self.failures = self.lockouts = 0
            self.next_try = 0.0

    def set(self, pin):
        if not isinstance(pin, str) or not re.fullmatch(r'\d{6,8}', pin):
            raise ValueError('Maintenance PIN Must be 6–8 digits')
        if self.path is None:
            raise RuntimeError('Practice mode maintenance PIN fixed yes ' + self.practice_pin if self.practice_pin is not None
                               else 'has not been set for maintenance PIN')
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 600_000)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, {'salt': salt.hex(), 'digest': digest.hex(), 'iterations': 600_000})
        with self.lock:
            self._follow()

    def _check(self, pin):
        """Returns when the PIN is right; RuntimeError says why not (wrong, too many tries, none set)."""
        if not self.configured:
            raise RuntimeError('has not been set for maintenance PIN')
        if not isinstance(pin, str):
            pin = ''   # a number, null, a list: never right, counted like any wrong PIN
        with self.lock:
            self._follow()
            wait = self.next_try - time.monotonic()
            if wait > 0:
                raise RuntimeError(f'Too many attempts, please {int(wait) + 1} Try again in seconds')
        if self.practice_pin is not None:
            right = hmac.compare_digest(pin.encode(), self.practice_pin.encode())
        else:
            try:
                record = json.loads(self.path.read_text(encoding='utf-8'))
                computed = hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(record['salt']), record['iterations'])
                right = hmac.compare_digest(computed, bytes.fromhex(record['digest']))
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                # The file went away or is not a PIN file: the same answer as "none", and the same way out.
                raise RuntimeError('has not been set for maintenance PIN') from None
        with self.lock:
            if not right:
                self.failures += 1
                if self.failures >= 5:
                    # Few combinations: every further lockout doubles, up to 15 minutes.
                    self.next_try = time.monotonic() + min(900, 30 * 2 ** self.lockouts)
                    self.lockouts += 1
                    self.failures = 0
                raise RuntimeError('Maintenance PIN mistake')
            self.failures = self.lockouts = 0

    def unlock(self, pin):
        self._check(pin)
        with self.lock:
            now = time.monotonic()
            self.sessions = {key: end for key, end in self.sessions.items() if end > now}
            session = secrets.token_urlsafe(32)
            self.sessions[session] = now + self.SESSION_SECONDS
            return session

    def verify(self, pin):
        """The PIN typed once more (deleting a Recipe): checked like unlock(), opens no session."""
        self._check(pin)
        return True

    def allowed(self, session):
        """True when the session was unlocked within the last 10 minutes, and that renews it.

        For an action that needs the PIN and is about to be carried out. No PIN set: never.
        """
        if not self.configured:
            return False
        with self.lock:
            self._follow()
            if self.sessions.get(session, 0) <= time.monotonic():
                self.sessions.pop(session, None)
                return False
            self.sessions[session] = time.monotonic() + self.SESSION_SECONDS
            return True

    def peek(self, session):
        """Seconds the session still has, 0 when it is not valid. Looking does not renew it."""
        if not self.configured:
            return 0
        with self.lock:
            self._follow()
            left = self.sessions.get(session, 0) - time.monotonic()
        return max(0, math.ceil(left))

    def lock_session(self, session):
        with self.lock:
            self.sessions.pop(session, None)
