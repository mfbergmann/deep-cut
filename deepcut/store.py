"""SQLite state. One file in /config; small enough to never need a migration tool."""
import json
import sqlite3
import threading
import time

from .config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    radarr_id   INTEGER PRIMARY KEY,
    title       TEXT,
    year        INTEGER,
    runtime     INTEGER,          -- minutes, 0 when unknown
    director    TEXT,
    last_scan   REAL,
    dismissed   INTEGER DEFAULT 0 -- 1 = never search again
);
CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    radarr_id   INTEGER,
    source      TEXT,             -- 'archive' | 'youtube'
    ext_id      TEXT,             -- IA identifier or YouTube id
    title       TEXT,
    url         TEXT,             -- page a person can open
    preview     TEXT,             -- embeddable player URL
    download    TEXT,             -- what the downloader fetches
    duration    REAL,             -- seconds
    score       INTEGER,
    reasons     TEXT,             -- JSON list of strings
    meta        TEXT,             -- JSON, source-specific
    state       TEXT DEFAULT 'new', -- new|rejected|queued|downloading|imported|failed
    error       TEXT,
    created     REAL,
    updated     REAL,
    UNIQUE (radarr_id, source, ext_id)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init():
    with _lock, connect() as con:
        con.executescript(SCHEMA)


def kv_get(key, default=None):
    with connect() as con:
        row = con.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return json.loads(row["v"]) if row else default


def kv_set(key, value):
    with _lock, connect() as con:
        con.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (key, json.dumps(value)))


def upsert_movie(m):
    with _lock, connect() as con:
        con.execute(
            """INSERT INTO movies (radarr_id, title, year, runtime, director)
               VALUES (:radarr_id, :title, :year, :runtime, :director)
               ON CONFLICT(radarr_id) DO UPDATE SET
                 title=excluded.title, year=excluded.year,
                 runtime=excluded.runtime, director=excluded.director""",
            m,
        )


def mark_scanned(radarr_id):
    with _lock, connect() as con:
        con.execute("UPDATE movies SET last_scan=? WHERE radarr_id=?", (time.time(), radarr_id))


def movie(radarr_id):
    with connect() as con:
        row = con.execute("SELECT * FROM movies WHERE radarr_id=?", (radarr_id,)).fetchone()
    return dict(row) if row else None


def set_dismissed(radarr_id, value):
    with _lock, connect() as con:
        con.execute("UPDATE movies SET dismissed=? WHERE radarr_id=?", (1 if value else 0, radarr_id))


def add_candidate(c, insert=True):
    """Insert a candidate unless this source item is already known for this film.

    A rejected candidate must stay rejected across rescans, so an existing row
    is never overwritten except to refresh its score while it is still 'new'.
    """
    now = time.time()
    with _lock, connect() as con:
        cur = con.execute(
            "SELECT id, state FROM candidates WHERE radarr_id=? AND source=? AND ext_id=?",
            (c["radarr_id"], c["source"], c["ext_id"]),
        ).fetchone()
        if cur:
            if cur["state"] == "new":
                con.execute(
                    "UPDATE candidates SET score=?, reasons=?, updated=? WHERE id=?",
                    (c["score"], json.dumps(c["reasons"]), now, cur["id"]),
                )
            return False
        if not insert:
            return False
        con.execute(
            """INSERT INTO candidates
               (radarr_id, source, ext_id, title, url, preview, download, duration,
                score, reasons, meta, state, created, updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'new',?,?)""",
            (
                c["radarr_id"], c["source"], c["ext_id"], c["title"], c["url"],
                c["preview"], c["download"], c["duration"], c["score"],
                json.dumps(c["reasons"]), json.dumps(c.get("meta", {})), now, now,
            ),
        )
        return True


def candidate(cid):
    with connect() as con:
        row = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    return _cand(row) if row else None


def set_state(cid, state, error=None):
    with _lock, connect() as con:
        con.execute(
            "UPDATE candidates SET state=?, error=?, updated=? WHERE id=?",
            (state, error, time.time(), cid),
        )


def _cand(row):
    d = dict(row)
    d["reasons"] = json.loads(d["reasons"] or "[]")
    d["meta"] = json.loads(d["meta"] or "{}")
    return d


def overview():
    """Everything the review page needs, grouped by film."""
    with connect() as con:
        movies = [dict(r) for r in con.execute("SELECT * FROM movies ORDER BY title")]
        cands = [_cand(r) for r in con.execute("SELECT * FROM candidates ORDER BY score DESC")]
    by_movie = {}
    for c in cands:
        by_movie.setdefault(c["radarr_id"], []).append(c)
    for m in movies:
        m["candidates"] = by_movie.get(m["radarr_id"], [])
    return movies


def drop_movies_not_in(ids):
    """Forget films Radarr no longer lists as missing, keeping imported history."""
    ids = set(ids)
    with _lock, connect() as con:
        rows = con.execute("SELECT radarr_id FROM movies").fetchall()
        for r in rows:
            rid = r["radarr_id"]
            if rid in ids:
                continue
            busy = con.execute(
                "SELECT 1 FROM candidates WHERE radarr_id=? AND state IN ('imported','queued','downloading')",
                (rid,),
            ).fetchone()
            if busy:
                continue
            con.execute("DELETE FROM candidates WHERE radarr_id=?", (rid,))
            con.execute("DELETE FROM movies WHERE radarr_id=?", (rid,))
