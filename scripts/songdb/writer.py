"""Writer — schema management + UPSERT with sibling inheritance.

Clean-room implementation from SPEC.md. No reference to upstream code.

Behavior summary (see SPEC §"INSERT logic"):

- ``ensure_schema``: create ``song`` and ``folder`` tables if missing.
  ``ALTER TABLE ... ADD COLUMN`` for columns that exist but lag the
  spec'd shape. Never drop tables.
- ``upsert``: ``INSERT OR REPLACE`` keyed on (md5, sha256). Before
  inserting, look up an existing sibling in the same directory and copy
  its ``folder`` / ``parent`` CRC onto the incoming row — this works
  around the JVM-default-charset problem (SPEC §13).
- ``favorite`` and ``adddate`` are preserved from any prior row.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Iterable, Optional

from .songdata import SongRow, COLUMN_ORDER


SONG_SCHEMA = """
CREATE TABLE IF NOT EXISTS song(
    md5 TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    title TEXT,
    subtitle TEXT,
    genre TEXT,
    artist TEXT,
    subartist TEXT,
    tag TEXT,
    path TEXT NOT NULL,
    folder TEXT,
    stagefile TEXT,
    banner TEXT,
    backbmp TEXT,
    preview TEXT,
    parent TEXT,
    level INTEGER,
    difficulty INTEGER,
    maxbpm INTEGER,
    minbpm INTEGER,
    length INTEGER,
    mode INTEGER,
    judge INTEGER,
    feature INTEGER,
    content INTEGER,
    date INTEGER,
    favorite INTEGER,
    adddate INTEGER,
    notes INTEGER,
    charthash TEXT,
    PRIMARY KEY(md5, sha256)
);
"""

FOLDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS folder(
    title TEXT,
    subtitle TEXT,
    command TEXT,
    path TEXT NOT NULL PRIMARY KEY,
    banner TEXT,
    parent TEXT,
    type INTEGER,
    date INTEGER,
    adddate INTEGER,
    max INTEGER
);
"""


# Expected (column_name, sql_type) pairs in case we have to ALTER.
SONG_COLS = [
    ('md5','TEXT'),('sha256','TEXT'),('title','TEXT'),('subtitle','TEXT'),
    ('genre','TEXT'),('artist','TEXT'),('subartist','TEXT'),('tag','TEXT'),
    ('path','TEXT'),('folder','TEXT'),('stagefile','TEXT'),('banner','TEXT'),
    ('backbmp','TEXT'),('preview','TEXT'),('parent','TEXT'),
    ('level','INTEGER'),('difficulty','INTEGER'),('maxbpm','INTEGER'),
    ('minbpm','INTEGER'),('length','INTEGER'),('mode','INTEGER'),
    ('judge','INTEGER'),('feature','INTEGER'),('content','INTEGER'),
    ('date','INTEGER'),('favorite','INTEGER'),('adddate','INTEGER'),
    ('notes','INTEGER'),('charthash','TEXT'),
]


def ensure_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute(SONG_SCHEMA)
    cur.execute(FOLDER_SCHEMA)
    # Detect and add missing columns on a legacy DB.
    cur.execute("PRAGMA table_info(song)")
    existing = {row[1] if isinstance(row[1], str) else row[1].decode() for row in cur.fetchall()}
    for name, typ in SONG_COLS:
        if name not in existing:
            cur.execute(f"ALTER TABLE song ADD COLUMN {name} {typ}")
    con.commit()


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.text_factory = bytes  # paths can hold non-utf8 bytes; we manage strings.
    return con


def open_db(db_path: str) -> sqlite3.Connection:
    """Open the DB, ensure schema, and return the connection.

    The caller owns the lifecycle — wrap in ``with contextlib.closing``.
    """
    con = _connect(db_path)
    ensure_schema(con)
    return con


def _lookup_existing(con: sqlite3.Connection, md5: str, sha256: str):
    """Return (favorite, adddate) of an existing PK row, or None."""
    cur = con.cursor()
    cur.execute(
        "SELECT favorite, adddate FROM song WHERE md5=? AND sha256=?",
        (md5.encode('ascii'), sha256.encode('ascii')),
    )
    r = cur.fetchone()
    if not r:
        return None
    fav = int(r[0]) if r[0] is not None else 0
    add = int(r[1]) if r[1] is not None else 0
    return fav, add


def _lookup_sibling(con: sqlite3.Connection, directory: str):
    """Return (folder, parent) CRC pair from any sibling row, or None."""
    cur = con.cursor()
    # Beatoraja stores `path` as UTF-8 — confirmed by DB inspection.
    try:
        pat = (directory + os.sep + '%').encode('utf-8')
    except UnicodeEncodeError:
        return None
    cur.execute(
        "SELECT folder, parent FROM song WHERE path LIKE ? LIMIT 1",
        (pat,),
    )
    r = cur.fetchone()
    if not (r and r[0]):
        return None
    try:
        f = r[0].decode('ascii') if isinstance(r[0], bytes) else str(r[0])
        p = (r[1].decode('ascii') if isinstance(r[1], (bytes, bytearray))
             else (str(r[1]) if r[1] else ''))
    except UnicodeDecodeError:
        return None
    return f, p


def _row_to_bytes(row: SongRow):
    """Serialize a SongRow tuple for sqlite3 binding.

    Beatoraja stores all text columns (including ``path``) as UTF-8
    bytes — verified by inspecting the live DB. Numeric columns pass
    through as ints.
    """
    out = []
    for col in COLUMN_ORDER:
        v = getattr(row, col)
        if isinstance(v, str):
            out.append(v.encode('utf-8', errors='replace'))
        else:
            out.append(v)
    return tuple(out)


def upsert(con: sqlite3.Connection, row: SongRow) -> None:
    """INSERT OR REPLACE the row, preserving favorite/adddate and applying
    sibling inheritance for ``folder`` / ``parent`` CRCs.
    """
    # Sibling inheritance — SPEC §"Sibling inheritance".
    directory = os.path.dirname(row.path)
    sib = _lookup_sibling(con, directory)
    if sib:
        row.folder, row.parent = sib

    existing = _lookup_existing(con, row.md5, row.sha256)
    if existing:
        ex_fav, ex_add = existing
        if row.favorite == 0 and ex_fav:
            row.favorite = ex_fav
        if row.adddate == 0 and ex_add:
            row.adddate = ex_add
        elif ex_add and ex_add < row.adddate:
            # Preserve original add-date when present.
            row.adddate = ex_add
    if row.adddate == 0:
        row.adddate = int(time.time())

    placeholders = ','.join('?' * len(COLUMN_ORDER))
    cols = ','.join(COLUMN_ORDER)
    cur = con.cursor()
    cur.execute(
        f"INSERT OR REPLACE INTO song({cols}) VALUES({placeholders})",
        _row_to_bytes(row),
    )


def upsert_many(con: sqlite3.Connection, rows: Iterable[SongRow]) -> int:
    n = 0
    for r in rows:
        upsert(con, r)
        n += 1
    con.commit()
    return n
