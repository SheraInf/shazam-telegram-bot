"""Small persistent store for this bot's own users and media IDs."""
import json
import sqlite3
import time
import re


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, alive INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, first_seen INTEGER NOT NULL, ref TEXT);
        CREATE TABLE IF NOT EXISTS refs (code TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def touch(self, chat_id, kind, user_id=None, ref=None):
        if ref and not self.db.execute("SELECT 1 FROM refs WHERE code=? AND active=1", (ref,)).fetchone():
            ref = None
        with self.db:
            self.db.execute("INSERT INTO chats VALUES (?,?,1) ON CONFLICT(id) DO UPDATE SET alive=1", (chat_id, kind))
            if user_id is not None:
                self.db.execute("INSERT OR IGNORE INTO users VALUES (?,?,?)", (user_id, int(time.time()), ref))

    def media(self, key):
        row = self.db.execute("SELECT data FROM media WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_media(self, key, data):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO media VALUES (?,?)", (key, json.dumps(data)))

    def ref(self, code, name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", code):
            raise ValueError("Код ссылки: 1–40 латинских букв, цифр, _ или -.")
        with self.db:
            self.db.execute("INSERT INTO refs VALUES (?,?,1)", (code, name[:100]))

    def refs(self):
        return self.db.execute("SELECT r.code,r.name,r.active,COUNT(u.id) FROM refs r LEFT JOIN users u ON u.ref=r.code GROUP BY r.code").fetchall()

    def disable_ref(self, code):
        with self.db:
            self.db.execute("UPDATE refs SET active=0 WHERE code=?", (code,))

    def stats(self):
        users = self.db.execute("SELECT COUNT(*) FROM chats WHERE kind='private' AND alive=1").fetchone()[0]
        groups = self.db.execute("SELECT COUNT(*) FROM chats WHERE kind!='private' AND alive=1").fetchone()[0]
        return users, groups

    def recipients(self):
        return [r[0] for r in self.db.execute("SELECT id FROM chats WHERE kind='private' AND alive=1")]

    def blocked(self, chat_id):
        with self.db:
            self.db.execute("UPDATE chats SET alive=0 WHERE id=?", (chat_id,))
