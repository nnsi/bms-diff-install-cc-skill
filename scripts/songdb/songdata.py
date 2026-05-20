"""SongRow projection.

Clean-room implementation from SPEC.md. No reference to upstream code.

Maps a parsed :class:`BMSModel` plus filesystem signals onto the column
set listed in SPEC §"SongRow population". Empirically-derived
transforms (judge, feature bits, content bits) live here too — see
inline comments for the reasoning.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from . import hashing
from .model import (
    BMSModel, NOTE_NORMAL, NOTE_LONG, NOTE_CHARGE, NOTE_HELL,
    NOTE_MINE, NOTE_HIDDEN, NOTE_LONG_END,
)
from .mode import Mode


# ---------------------------------------------------------------------------
# Feature bits (derived empirically from D:/bms/beatoraja/songdata.db
# observations — see comments in commit log / README. Bit layout:
#
#   bit 0 (1)   UNDEFINEDLN     LN present without #LNMODE specified
#   bit 1 (2)   MINENOTE        mine notes present (Dx / Ex channels)
#   bit 2 (4)   RANDOM          #RANDOM blocks present
#   bit 3 (8)   LONGNOTE        #LNMODE 1 explicitly set
#   bit 4 (16)  CHARGENOTE      #LNMODE 2
#   bit 5 (32)  HELLCHARGENOTE  #LNMODE 3
#   bit 6 (64)  STOPSEQUENCE    #STOPxx defs present
#   bit 7 (128) SCROLL          #SCROLLxx defs present
# ---------------------------------------------------------------------------
FEATURE_UNDEFINEDLN    = 1 << 0
FEATURE_MINENOTE       = 1 << 1
FEATURE_RANDOM         = 1 << 2
FEATURE_LONGNOTE       = 1 << 3
FEATURE_CHARGENOTE     = 1 << 4
FEATURE_HELLCHARGENOTE = 1 << 5
FEATURE_STOPSEQUENCE   = 1 << 6
FEATURE_SCROLL         = 1 << 7

# Content bits derived from the same DB:
#   bit 0 (1)   TEXT       folder contains a .txt file
#   bit 1 (2)   BGA        chart has #BMPxx definitions
#   bit 7 (128) NOKEYSOUND fewer keysound assignments than visible notes
CONTENT_TEXT       = 1 << 0
CONTENT_BGA        = 1 << 1
CONTENT_NOKEYSOUND = 1 << 7


DIFFICULTY_KEYWORDS = [
    (re.compile(r'\bleggendaria\b', re.I), 5),
    (re.compile(r'\binsane\b',      re.I), 5),
    (re.compile(r'\banother\b',     re.I), 4),
    (re.compile(r'\bhyper\b',       re.I), 3),
    (re.compile(r'\bnormal\b',      re.I), 2),
    (re.compile(r'\bbeginner\b',    re.I), 1),
    # CJK common labels — keep last, optional.
    (re.compile(r'(易|EASY)',       re.I), 2),
]


def _infer_difficulty(m: BMSModel, notes: int) -> int:
    if m.difficulty:
        return m.difficulty
    # Try subtitle first, then title+subtitle.
    for src in (m.subtitle, m.title + ' ' + m.subtitle):
        for rx, d in DIFFICULTY_KEYWORDS:
            if rx.search(src):
                return d
    # Notes-based fallback.
    if notes < 250: return 1
    if notes < 600: return 2
    if notes < 1000: return 3
    if notes < 2000: return 4
    return 5


# ---------------------------------------------------------------------------
# Judge transform — empirically derived from
# ``SELECT mode, judge, COUNT(*) FROM song WHERE judge>0 GROUP BY mode,
# judge``. The dominant cluster per #RANK was:
#   #RANK 0 → judge 25
#   #RANK 1 → judge 50
#   #RANK 2 → judge 75
#   #RANK 3 → judge 100
#   #RANK 4 → judge 75   (treated as RANK 2 by the runtime; rare in DB)
# Files without #RANK fall back to 75 (NORMAL). #DEFEXRANK V overrides:
#   judge = floor(75 * V / 100)
# ---------------------------------------------------------------------------
RANK_TO_JUDGE = {0: 25, 1: 50, 2: 75, 3: 100, 4: 75}


def _compute_judge(m: BMSModel) -> int:
    # BMSON path: ``info.judge_rank`` is already a percentage.
    if m.judge_rank_pct is not None and m.judge_rank_pct > 0:
        return m.judge_rank_pct
    # DEFEXRANK override (BMS).
    if m.defexrank is not None and m.defexrank > 0:
        return int(75 * m.defexrank / 100)
    if m.raw_rank is None:
        return 75
    return RANK_TO_JUDGE.get(m.raw_rank, 75)


def _compute_feature(m: BMSModel) -> int:
    bits = 0
    has_ln = any(n.kind in (NOTE_LONG, NOTE_CHARGE, NOTE_HELL) for n in m.notes)
    has_mine = any(n.kind == NOTE_MINE for n in m.notes)
    if m.has_random:
        bits |= FEATURE_RANDOM
    if has_mine:
        bits |= FEATURE_MINENOTE
    if m.has_stop_seq:
        bits |= FEATURE_STOPSEQUENCE
    if m.has_scroll_seq:
        bits |= FEATURE_SCROLL
    if has_ln:
        # Pick the bit corresponding to the LN mode declared in the file.
        # If #LNMODE is unset (0) we use the UNDEFINEDLN bit — matches the
        # empirical clusters at feature=1 (LN without LNMODE) vs feature=8
        # (LN with #LNMODE 1).
        if m.lnmode == 1:
            bits |= FEATURE_LONGNOTE
        elif m.lnmode == 2:
            bits |= FEATURE_CHARGENOTE
        elif m.lnmode == 3:
            bits |= FEATURE_HELLCHARGENOTE
        else:
            bits |= FEATURE_UNDEFINEDLN
    return bits


def _compute_content(m: BMSModel, path: str) -> int:
    bits = 0
    folder = os.path.dirname(path)
    # bit 0 = TEXT file present.
    has_txt = False
    try:
        for f in os.listdir(folder):
            if f.lower().endswith('.txt'):
                has_txt = True
                break
    except OSError:
        pass
    if has_txt:
        bits |= CONTENT_TEXT

    # bit 1 = BGA / BMP defs.
    if m.has_bmp_defs:
        bits |= CONTENT_BGA

    # bit 7 = NOKEYSOUND. Empirical hint from SPEC: chart with length ≥ 30s
    # but very few WAV defs. From sampling, this fires only when the
    # author defined a tiny pool of keysounds (<=10 distinct entries) for
    # a non-trivial chart — typical of "no-keysound" practice charts.
    if m.visible_note_count > 100 and m.wav_def_count > 0 and m.wav_def_count <= 10:
        bits |= CONTENT_NOKEYSOUND
    # No WAV defs at all but real notes — also NOKEYSOUND.
    elif m.visible_note_count > 100 and m.wav_def_count == 0:
        bits |= CONTENT_NOKEYSOUND
    return bits


# ---------------------------------------------------------------------------
# Notes / length empirical rules
# ---------------------------------------------------------------------------

def _count_notes(m: BMSModel) -> int:
    """Empirically derived: count visible judgement-bearing notes.

    Includes: normal notes, LN starts (NOTE_LONG / NOTE_CHARGE / NOTE_HELL).
    Excludes: hidden, LN-end markers (NOTE_LONG_END), mines.
    """
    return sum(
        1 for n in m.notes
        if n.kind in (NOTE_NORMAL, NOTE_LONG, NOTE_CHARGE, NOTE_HELL)
    )


def _compute_length_ms(m: BMSModel) -> int:
    """Length = ms of the latest meaningful timeline event.

    SPEC §"Length & note count": include visible/hidden/BGM/BGA events
    in addition to notes themselves. The events list is built by the
    parser as it walks the chart.
    """
    latest = 0
    for ev in m.events:
        if ev.time_us > latest:
            latest = ev.time_us
    for n in m.notes:
        end_us = n.time_us + n.length_us
        if end_us > latest:
            latest = end_us
    return int(latest / 1000)


# ---------------------------------------------------------------------------
# Public SongRow
# ---------------------------------------------------------------------------

@dataclass
class SongRow:
    md5: str = ''
    sha256: str = ''
    title: str = ''
    subtitle: str = ''
    genre: str = ''
    artist: str = ''
    subartist: str = ''
    tag: str = ''
    path: str = ''
    folder: str = ''
    stagefile: str = ''
    banner: str = ''
    backbmp: str = ''
    preview: str = ''
    parent: str = ''
    level: int = 0
    difficulty: int = 0
    maxbpm: int = 0
    minbpm: int = 0
    length: int = 0
    mode: int = 0
    judge: int = 0
    feature: int = 0
    content: int = 0
    date: int = 0
    favorite: int = 0
    adddate: int = 0
    notes: int = 0
    charthash: str = ''   # always empty — SPEC §"Things we don't compute"


COLUMN_ORDER = (
    'md5','sha256','title','subtitle','genre','artist','subartist','tag',
    'path','folder','stagefile','banner','backbmp','preview','parent',
    'level','difficulty','maxbpm','minbpm','length','mode','judge',
    'feature','content','date','favorite','adddate','notes','charthash',
)


def build_song_row(path: str, model: BMSModel) -> SongRow:
    """Project a parsed chart + filesystem state into a SongRow.

    Folder/parent CRCs use our local encoding heuristic. ``writer.upsert``
    will replace them with sibling-inherited values on PK collision.
    """
    abs_path = os.path.abspath(path).replace('/', '\\')
    parent_dir = os.path.dirname(abs_path)
    grand_dir = os.path.dirname(parent_dir)

    is_bmson = abs_path.lower().endswith('.bmson')
    md5 = '' if is_bmson else hashing.md5_file(abs_path)
    sha256 = hashing.sha256_file(abs_path)
    folder = hashing.crc32_path(parent_dir)
    parent = hashing.crc32_path(grand_dir)
    try:
        mtime = int(os.path.getmtime(abs_path))
    except OSError:
        mtime = 0

    notes = _count_notes(model)
    difficulty = _infer_difficulty(model, notes)

    row = SongRow(
        md5=md5,
        sha256=sha256,
        title=model.title,
        subtitle=model.subtitle,
        genre=model.genre,
        artist=model.artist,
        subartist=model.subartist,
        tag='',
        path=abs_path,
        folder=folder,
        stagefile=model.stagefile,
        banner=model.banner,
        backbmp=model.backbmp,
        preview=model.preview,
        parent=parent,
        level=model.playlevel,
        difficulty=difficulty,
        maxbpm=int(model.max_bpm) if model.max_bpm else 0,
        minbpm=int(model.min_bpm) if model.min_bpm else 0,
        length=_compute_length_ms(model),
        mode=model.mode.id,
        judge=_compute_judge(model),
        feature=_compute_feature(model),
        content=_compute_content(model, abs_path),
        date=mtime,
        favorite=0,
        adddate=int(time.time()),
        notes=notes,
        charthash='',
    )
    return row
