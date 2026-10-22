import secrets
import sqlite3
import threading
import time

from .config import DEFAULT_SETTINGS, PAIRING_CODE_TTL_SECONDS, READINGS_RETENTION_DAYS

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_seen REAL,
    level INTEGER,
    charging INTEGER,
    temperature REAL,
    voltage REAL
);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    level INTEGER NOT NULL,
    charging INTEGER NOT NULL,
    temperature REAL,
    voltage REAL
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device_id, ts);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Database:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(devices)")}
        for column, ddl in (
            ("pairing_code", "TEXT"),
            ("pairing_expires", "REAL"),
            ("show_in_tray", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE devices ADD COLUMN {column} {ddl}")
        self._conn.commit()

    def _execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _new_token():
        return secrets.token_urlsafe(24)

    def add_device(self, name):
        """Cria o dispositivo já com um código de pareamento ativo."""
        cur = self._execute(
            "INSERT INTO devices (name, token, created_at) VALUES (?, ?, ?)",
            (name, self._new_token(), time.time()),
        )
        self.issue_pairing_code(cur.lastrowid)
        return self.get_device(cur.lastrowid)

    def issue_pairing_code(self, device_id):
        """Gera (ou renova) o código de 6 dígitos; devolve (código, expira_em)."""
        expires = time.time() + PAIRING_CODE_TTL_SECONDS
        with self._lock:
            active = {
                r[0] for r in self._conn.execute(
                    "SELECT pairing_code FROM devices WHERE pairing_code IS NOT NULL AND pairing_expires > ?",
                    (time.time(),),
                )
            }
            code = f"{secrets.randbelow(1_000_000):06d}"
            while code in active:
                code = f"{secrets.randbelow(1_000_000):06d}"
            self._conn.execute(
                "UPDATE devices SET pairing_code = ?, pairing_expires = ? WHERE id = ?",
                (code, expires, device_id),
            )
            self._conn.commit()
        return code, expires

    def complete_pairing(self, code):
        """Troca um código válido por um token novo (uso único).

        O token anterior do dispositivo é descartado, então refazer o pareamento
        revoga o aparelho antigo. Devolve o dispositivo ou None se o código não vale.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM devices WHERE pairing_code = ? AND pairing_expires > ?",
                (code, time.time()),
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE devices SET token = ?, pairing_code = NULL, pairing_expires = NULL WHERE id = ?",
                (self._new_token(), row["id"]),
            )
            self._conn.commit()
            device = self._conn.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
            return dict(device)

    def get_device(self, device_id):
        rows = self._query("SELECT * FROM devices WHERE id = ?", (device_id,))
        return rows[0] if rows else None

    def get_device_by_token(self, token):
        rows = self._query("SELECT * FROM devices WHERE token = ?", (token,))
        return rows[0] if rows else None

    def list_devices(self):
        return self._query("SELECT * FROM devices ORDER BY name COLLATE NOCASE")

    def rename_device(self, device_id, name):
        self._execute("UPDATE devices SET name = ? WHERE id = ?", (name, device_id))

    def set_show_in_tray(self, device_id, show):
        self._execute("UPDATE devices SET show_in_tray = ? WHERE id = ?", (int(show), device_id))

    def delete_device(self, device_id):
        self._execute("DELETE FROM devices WHERE id = ?", (device_id,))

    def record_reading(self, device_id, level, charging, temperature=None, voltage=None):
        now = time.time()
        self._execute(
            "UPDATE devices SET last_seen=?, level=?, charging=?, temperature=?, voltage=? WHERE id=?",
            (now, level, int(charging), temperature, voltage, device_id),
        )
        self._execute(
            "INSERT INTO readings (device_id, ts, level, charging, temperature, voltage) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, now, level, int(charging), temperature, voltage),
        )
        cutoff = now - READINGS_RETENTION_DAYS * 86400
        self._execute("DELETE FROM readings WHERE ts < ?", (cutoff,))

    def get_setting(self, key):
        rows = self._query("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else DEFAULT_SETTINGS[key]

    def set_setting(self, key, value):
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
