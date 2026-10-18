#!/usr/bin/env python3
"""Plasmic electronic control - alarm (SPEC B.9) .

The alarm is recorded on this side of the program (data\\alarms.json) ,is not recorded in the browser: what happens when no one is watching the screen, open it the next day When
can still be seen; it can also be remembered after pressing ”Confirm”. Each alarm has a key,the same key There is only one transaction at the same time.

three kinds: 
  condition  If the condition is met, it will be restored. If it is not met, it will be restored. The program that issued it is responsible for clearing it. When the program restarts, the
             This type is all considered to have been restored first; if the conditions are still there, the responsible program will be sent again.
  event      Something happened once. Click ”Confirm” and it will disappear. The content of
  latched    Will not be cleared until the specified action is successful (for example, the pump control board is reconnected, high pressure readback is turned off).

Those that have been restored but have not yet been confirmed will remain on the list (Restored · Unconfirmed); only those that have been confirmed and restored will be removed.
Write one event each for occurrence, recovery and confirmation.
"""
from __future__ import annotations

import json
import threading

from store import now_iso

PRIORITY_NAME = {1: 'Emergency', 2: 'High', 3: 'Medium'}
KINDS = ('condition', 'event', 'latched')
SHOWN = 100  # most alarms sent to the page in one state reply


class Alarms:
    def __init__(self, store):
        self.store = store
        self.path = store.dir / 'alarms.json'
        self.lock = threading.Lock()
        self.next_id = 1
        self.rows = []  # active, or cleared and not yet acknowledged
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            saved = {}
        saved = saved if isinstance(saved, dict) else {}
        changed = False
        for row in saved.get('alarms') if isinstance(saved.get('alarms'), list) else []:
            row = self._checked(row)
            if row is None or any(other['key'] == row['key'] or other['id'] == row['id'] for other in self.rows):
                changed = True
                continue
            if row['kind'] == 'condition' and not row['cleared']:
                # Nobody knows whether it still holds: its owner raises it again if it does.
                changed = True
                if row['acked']:
                    continue
                row.update(cleared=True, cleared_at=now_iso())
            self.rows.append(row)
        highest = max([row['id'] for row in self.rows], default=0)
        stored = saved.get('next_id')
        self.next_id = max(stored if type(stored) is int else 1, highest + 1)
        if changed:
            notes = []
            with self.lock:
                self._save(notes)
            self._tell(notes)

    @staticmethod
    def _checked(row):
        """A stored alarm with every field in place, or None for something else."""
        if (not isinstance(row, dict) or type(row.get('id')) is not int or not isinstance(row.get('key'), str)
                or row.get('priority') not in PRIORITY_NAME or row.get('kind') not in KINDS):
            return None
        return {'id': row['id'], 'key': row['key'], 'priority': row['priority'], 'area': str(row.get('area', '')),
                'text': str(row.get('text', '')), 'kind': row['kind'], 'since': str(row.get('since', '')),
                'acked': row.get('acked') is True, 'acked_at': row.get('acked_at'),
                'cleared': row.get('cleared') is True, 'cleared_at': row.get('cleared_at')}

    def _save(self, notes):
        """Caller holds the lock. A file that cannot be written is said once in the event log; the list goes on."""
        try:
            self.store.write_json(self.path, {'next_id': self.next_id, 'alarms': self.rows})
        except OSError as exc:
            notes.append((f'The alarm record was not archived successfully:{exc}', 'warn', None))

    def _tell(self, notes):
        for text, level, actor in notes:
            self.store.event(text, level, actor)

    def _find(self, key):
        for row in self.rows:
            if row['key'] == key:
                return row
        return None

    def raise_(self, key, priority, area, text, kind='event'):
        """Make the alarm ``key`` active; returns its id.

        priority 1 Emergency · 2 high · 3 middle. Raising an alarm that is active already changes nothing, unless its
        text changed or it became more serious: then it must be acknowledged again.
        """
        if type(priority) is not int or priority not in PRIORITY_NAME or kind not in KINDS or not key:
            raise ValueError('raise_(key, priority 1-3, area, text, kind in %r)' % (KINDS,))
        key, area, text = str(key)[:120], str(area)[:20], str(text)[:400]
        notes = []
        with self.lock:
            row = self._find(key)
            if row is not None and not row['cleared']:
                news = text != row['text'] or priority < row['priority']
                if not news and (priority, area, kind) == (row['priority'], row['area'], row['kind']):
                    return row['id']
                row.update(priority=priority, area=area, text=text, kind=kind)
                if news:
                    row.update(acked=False, acked_at=None)
            else:
                # A new occurrence gets a new id: a late "confirm" of the old one cannot acknowledge it.
                if row is not None:
                    self.rows.remove(row)
                row = {'id': self.next_id, 'key': key, 'priority': priority, 'area': area, 'text': text, 'kind': kind,
                       'since': now_iso(), 'acked': False, 'acked_at': None, 'cleared': False, 'cleared_at': None}
                self.next_id += 1
                self.rows.append(row)
                news = True
            if news:
                notes.append((f'Alarm ({PRIORITY_NAME[priority]} · {area.title()}): {text}',
                              'error' if priority < 3 else 'warn', None))
            self._save(notes)
            found = row['id']
        self._tell(notes)
        return found

    def clear(self, key):
        """The condition behind ``key`` is over. True when an active alarm was cleared."""
        notes = []
        with self.lock:
            row = self._find(str(key))
            if row is None or row['cleared']:
                return False
            if row['acked']:
                self.rows.remove(row)
            else:
                row.update(cleared=True, cleared_at=now_iso())
            notes.append((f'Alarm cleared ({row["area"].title()}): {row["text"]}', 'info', None))
            self._save(notes)
        self._tell(notes)
        return True

    def ack(self, ids=None, actor=None):
        """Acknowledge the alarms with these ids (None: all of them); returns how many it were.

        ``actor`` (optional) is who pressed the button, for the event log.
        """
        wanted = None if ids is None else {value for value in ids if type(value) is int}
        notes, texts = [], []
        with self.lock:
            for row in list(self.rows):
                if row['acked'] or (wanted is not None and row['id'] not in wanted):
                    continue
                row.update(acked=True, acked_at=now_iso())
                texts.append(row['text'])
                if row['cleared'] or row['kind'] == 'event':  # an event is over the moment it has been seen
                    self.rows.remove(row)
            if texts:
                head = 'Acknowledged alarm: ' if len(texts) == 1 else f'Acknowledged {len(texts)} alarms: '
                notes.append((head + '; '.join(texts), 'info', '' if actor is None else actor))
                self._save(notes)
        self._tell(notes)
        return len(texts)

    def view(self):
        """state.alarms of API.md C.6: unacknowledged first, then by priority, then the newest first."""
        with self.lock:
            rows = [dict(row) for row in self.rows]
        rows.sort(key=lambda row: row['since'], reverse=True)
        rows.sort(key=lambda row: (row['acked'], row['priority']))
        open_ones = [row['priority'] for row in rows if not row['acked']]
        return {'worst': min(open_ones, default=None), 'unacked': len(open_ones),
                'counts': {f'p{priority}': sum(1 for row in rows if row['priority'] == priority)
                           for priority in PRIORITY_NAME},
                'active': [{key: row[key] for key in ('id', 'key', 'priority', 'area', 'text', 'since', 'acked', 'cleared')}
                           for row in rows[:SHOWN]]}
