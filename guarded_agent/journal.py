"""Durable at-most-once dispatch per operation and unresolved effect.

The reservation is committed BEFORE crossing the backend boundary. A crash in
that gap deliberately requires review; it is never interpreted as safe to retry.
"""
from dataclasses import asdict
import hashlib
import json
import sqlite3
import time
from .model import Action, Result



def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprint(action):
    data = action.to_dict()
    data.pop("operation_id")
    return digest(data)


def effect_key(action):
    # A new operation ID or changed expected result cannot bypass an unresolved
    # identical input. Resolution, not a retry flag, releases this lock.
    # Conservatively freeze further mutations in this bound window until the
    # unknown effect is reconciled. Changing selector/kind/text cannot evade it.
    return digest({"window": asdict(action.window)})


class EffectBlocked(RuntimeError):
    pass


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, plan TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS effect_locks (
                effect TEXT PRIMARY KEY, operation_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL,
                at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
        """)

    def begin(self, action: Action):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT fingerprint,status,result FROM operations WHERE id=?", (action.operation_id,)).fetchone()
            if row:
                if row[0] != fingerprint(action):
                    raise ValueError("operation_id reused for a different plan")
                if row[2]:
                    return Result(**json.loads(row[2]))
                return Result(action.operation_id, row[1], "unknown", "existing_incomplete_operation", needs_review=True)
            self.db.execute("INSERT INTO operations VALUES (?,?,?,?,NULL)",
                            (action.operation_id, fingerprint(action), json.dumps(action.to_dict(), ensure_ascii=False), "not_sent"))
            blocked = self.db.execute("SELECT operation_id FROM effect_locks WHERE effect=?", (effect_key(action),)).fetchone()
            if blocked:
                result = Result(action.operation_id, "not_sent", "unknown", "unresolved_effect:" + blocked[0], needs_review=True)
                self.db.execute("UPDATE operations SET result=? WHERE id=?", (json.dumps(result.to_dict()), action.operation_id))
                return result
        return None

    def reserve(self, action):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT operation_id FROM effect_locks WHERE effect=?", (effect_key(action),)).fetchone()
            if row:
                raise EffectBlocked("unresolved_effect:" + row[0])
            self.db.execute("INSERT INTO effect_locks VALUES (?,?)", (effect_key(action), action.operation_id))
            self.db.execute("UPDATE operations SET status='uncertain' WHERE id=?", (action.operation_id,))

    def record(self, operation_id, kind, data):
        with self.db:
            self.db.execute("INSERT INTO events(operation_id,at,kind,data) VALUES (?,?,?,?)",
                            (operation_id, time.time(), kind, json.dumps(data, ensure_ascii=False, default=str)))

    def window_blocker(self, window):
        row = self.db.execute("SELECT operation_id FROM effect_locks WHERE effect=?", (digest({"window": asdict(window)}),)).fetchone()
        return row[0] if row else None

    def receipt(self, operation_id, status):
        with self.db:
            self.db.execute("UPDATE operations SET status=? WHERE id=?", (status, operation_id))

    def finish(self, result):
        with self.db:
            self.db.execute("UPDATE operations SET status=?,result=? WHERE id=?",
                            (result.execution_status, json.dumps(result.to_dict(), ensure_ascii=False), result.operation_id))
            if result.outcome == "success" or result.execution_status == "not_sent":
                self.db.execute("DELETE FROM effect_locks WHERE operation_id=?", (result.operation_id,))
        return result

    def resolve(self, operation_id, resolution, evidence):
        """Explicit external reconciliation; never used by the executor.

        The operator must establish completed or definitely_not_executed using
        independent evidence. This method does not dispatch any input.
        """
        if resolution not in {"completed", "definitely_not_executed"} or not evidence.strip():
            raise ValueError("An explicit resolution and evidence are required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.db.execute("SELECT 1 FROM operations WHERE id=?", (operation_id,)).fetchone():
                raise KeyError(operation_id)
            self.db.execute("INSERT INTO events(operation_id,at,kind,data) VALUES (?,?,?,?)",
                            (operation_id, time.time(), "manual_resolution", json.dumps({"resolution": resolution, "evidence": evidence})))
            self.db.execute("DELETE FROM effect_locks WHERE operation_id=?", (operation_id,))
        # Preserve the original result and send facts. Use a NEW operation ID
        # only after resolution when another action is actually intended.

    def close(self):
        self.db.close()
