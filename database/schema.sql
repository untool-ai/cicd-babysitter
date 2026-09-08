PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS events (
 event_key TEXT PRIMARY KEY,
 delivery_id TEXT NOT NULL UNIQUE,
 payload_sha TEXT NOT NULL,
 received_at TEXT NOT NULL,
 event_json TEXT NOT NULL,
 decision_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
 action_key TEXT PRIMARY KEY,
 incident TEXT NOT NULL,
 repository TEXT NOT NULL,
 run_id INTEGER NOT NULL,
 attempt INTEGER NOT NULL,
 sha TEXT NOT NULL,
 event_key TEXT NOT NULL REFERENCES events(event_key),
 created REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','accepted','uncertain','rejected','verified_success','verified_failure'))
);
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 timestamp TEXT NOT NULL,
 kind TEXT NOT NULL,
 subject TEXT NOT NULL,
 detail TEXT NOT NULL,
 previous_hash TEXT NOT NULL,
 entry_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT,'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT,'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
