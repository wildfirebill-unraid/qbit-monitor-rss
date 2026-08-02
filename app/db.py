"""SQLite persistence for runtime state (seen RSS items, tracked torrents)."""

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    guid          TEXT PRIMARY KEY,
    feed_id       INTEGER,
    title         TEXT,
    link          TEXT,
    torrent_url   TEXT,
    pub_date      TEXT,
    state         TEXT DEFAULT 'pending',
    target_instance INTEGER,
    matched_at    REAL,
    info_hash     TEXT,
    torrent_hash  TEXT,
    error         TEXT
);
CREATE TABLE IF NOT EXISTS added_torrents (
    info_hash    TEXT PRIMARY KEY,
    instance_id  INTEGER,
    name         TEXT,
    added_at     REAL,
    source       TEXT
);
CREATE TABLE IF NOT EXISTS tracked_torrents (
    hash             TEXT,
    instance_id      INTEGER,
    title            TEXT,
    feed_id          INTEGER,
    added_at         REAL,
    slot_released    INTEGER DEFAULT 0,
    last_seed_seconds REAL DEFAULT 0,
    last_seen_at     REAL,
    PRIMARY KEY (hash, instance_id)
);
CREATE INDEX IF NOT EXISTS idx_tracked_instance ON tracked_torrents(instance_id);
CREATE INDEX IF NOT EXISTS idx_seen_feed ON seen_items(feed_id);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._conn = None

    def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        # migrations for pre-existing databases
        try:
            self._conn.execute("ALTER TABLE seen_items ADD COLUMN torrent_url TEXT")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _execute(self, sql, args=()):
        cur = self._conn.execute(sql, args)
        return cur

    # --- seen items ---------------------------------------------------------
    def item_exists(self, guid: str) -> bool:
        cur = self._execute("SELECT 1 FROM seen_items WHERE guid=?", (guid,))
        return cur.fetchone() is not None

    def insert_item(
        self,
        guid,
        feed_id,
        title,
        link,
        pub_date,
        target_instance,
        state="pending",
        torrent_url=None,
    ):
        self._execute(
            "INSERT OR IGNORE INTO seen_items "
            "(guid, feed_id, title, link, torrent_url, pub_date, state, target_instance, matched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                guid,
                feed_id,
                title,
                link,
                torrent_url,
                pub_date,
                state,
                target_instance,
                time.time(),
            ),
        )
        self._conn.commit()

    def update_item(self, guid, **fields):
        cols = {k: v for k, v in fields.items() if v is not None}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self._execute(
            f"UPDATE seen_items SET {sets} WHERE guid=?",
            tuple(cols.values()) + (guid,),
        )
        self._conn.commit()

    def items_for_feed(self, feed_id, limit=500):
        cur = self._execute(
            "SELECT * FROM seen_items WHERE feed_id=? ORDER BY matched_at DESC LIMIT ?",
            (feed_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def pending_items(self, feed_ids=None):
        if feed_ids:
            q = ",".join("?" * len(feed_ids))
            cur = self._execute(
                f"SELECT * FROM seen_items WHERE state='pending' AND feed_id IN ({q}) "
                "ORDER BY matched_at ASC",
                tuple(feed_ids),
            )
        else:
            cur = self._execute(
                "SELECT * FROM seen_items WHERE state='pending' ORDER BY matched_at ASC"
            )
        return [dict(r) for r in cur.fetchall()]

    def delete_items_for_feed(self, feed_id):
        self._execute("DELETE FROM seen_items WHERE feed_id=?", (feed_id,))
        self._conn.commit()

    # --- added / tracked torrents -------------------------------------------
    def hash_added_anywhere(self, info_hash: str) -> bool:
        cur = self._execute(
            "SELECT 1 FROM added_torrents WHERE info_hash=?", (info_hash,)
        )
        if cur.fetchone():
            return True
        cur = self._execute(
            "SELECT 1 FROM tracked_torrents WHERE hash=?", (info_hash,)
        )
        return cur.fetchone() is not None

    def title_added_anywhere(self, normalized: str) -> bool:
        cur = self._execute(
            "SELECT 1 FROM added_torrents WHERE name=? COLLATE NOCASE",
            (normalized,),
        )
        return cur.fetchone() is not None

    def record_added(self, info_hash, instance_id, name, source="rss"):
        self._execute(
            "INSERT OR IGNORE INTO added_torrents "
            "(info_hash, instance_id, name, added_at, source) VALUES (?,?,?,?,?)",
            (info_hash, instance_id, name, time.time(), source),
        )
        self._conn.commit()

    def track_torrent(self, info_hash, instance_id, title, feed_id):
        self._execute(
            "INSERT OR IGNORE INTO tracked_torrents "
            "(hash, instance_id, title, feed_id, added_at, slot_released, last_seen_at) "
            "VALUES (?,?,?,?,?,0,?)",
            (info_hash, instance_id, title, feed_id, time.time(), time.time()),
        )
        self._conn.commit()

    def tracked_all(self):
        cur = self._execute("SELECT * FROM tracked_torrents")
        return [dict(r) for r in cur.fetchall()]

    def update_tracked(self, info_hash, instance_id, **fields):
        cols = {k: v for k, v in fields.items() if v is not None}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        self._execute(
            f"UPDATE tracked_torrents SET {sets} WHERE hash=? AND instance_id=?",
            tuple(cols.values()) + (info_hash, instance_id),
        )
        self._conn.commit()

    def slots_in_use(self) -> int:
        cur = self._execute(
            "SELECT COUNT(*) AS c FROM tracked_torrents WHERE slot_released=0"
        )
        return cur.fetchone()["c"]
