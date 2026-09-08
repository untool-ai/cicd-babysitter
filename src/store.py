"""Transactional local ledger. Hash chaining is tamper evidence, not WORM storage."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript((Path(__file__).resolve().parents[1] / 'database/schema.sql').read_text())

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def audit(self, kind, subject, detail):
        now = datetime.now(timezone.utc).isoformat()
        row = self.db.execute('SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        previous = row[0] if row else '0' * 64
        detail_text = canonical(detail)
        digest = hashlib.sha256(canonical([now, kind, subject, detail_text, previous]).encode()).hexdigest()
        self.db.execute('INSERT INTO audit(timestamp,kind,subject,detail,previous_hash,entry_hash) VALUES(?,?,?,?,?,?)',
                        (now, kind, subject, detail_text, previous, digest))

    def record(self, key, delivery, digest, event, decision):
        with self.transaction():
            prior = self.db.execute('SELECT * FROM events WHERE delivery_id=?', (delivery,)).fetchone()
            if prior and prior['payload_sha'] != digest:
                raise ValueError('Conflicting delivery identifier')
            prior = prior or self.db.execute('SELECT * FROM events WHERE event_key=? OR payload_sha=?', (key, digest)).fetchone()
            if prior:
                return {'duplicate': True, 'event_key': prior['event_key'], 'decision': json.loads(prior['decision_json'])}
            self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                (key, delivery, digest, datetime.now(timezone.utc).isoformat(), canonical(event), canonical(decision)))
            self.audit('decision', key, decision)
            return {'duplicate': False, 'event_key': key, 'decision': decision}

    def verify(self):
        previous = '0' * 64
        for row in self.db.execute('SELECT * FROM audit ORDER BY seq'):
            digest = hashlib.sha256(canonical([row['timestamp'],row['kind'],row['subject'],row['detail'],previous]).encode()).hexdigest()
            if row['previous_hash'] != previous or row['entry_hash'] != digest:
                return False
            previous = digest
        return True

    def export(self):
        return {'events': [dict(row) for row in self.db.execute('SELECT * FROM events ORDER BY received_at,event_key')],
                'actions': [dict(row) for row in self.db.execute('SELECT * FROM actions ORDER BY created,action_key')],
                'audit': [dict(row) for row in self.db.execute('SELECT * FROM audit ORDER BY seq')],
                'chain_valid': self.verify(), 'immutability': 'local-tamper-evidence-only'}
