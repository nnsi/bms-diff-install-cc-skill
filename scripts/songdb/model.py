"""Data classes for the in-memory chart model.

Clean-room implementation from SPEC.md. No reference to upstream code.

The model is intentionally lightweight — just enough state to compute
the ``song`` columns described in SPEC §"SongRow population".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .mode import Mode, BEAT_7K


# --- note kinds ------------------------------------------------------------
NOTE_NORMAL    = 0
NOTE_LONG      = 1  # plain LN (#LNTYPE 1 or default LNOBJ)
NOTE_CHARGE    = 2  # CN (#LNTYPE 2 / #LNMODE 2)
NOTE_HELL      = 3  # HCN (#LNTYPE 3 / #LNMODE 3)
NOTE_MINE      = 4
NOTE_HIDDEN    = 5  # invisible note (3x/4x channels)
NOTE_LONG_END  = 6  # LN end marker (counts differently)


@dataclass
class Note:
    lane: int
    time_us: int
    kind: int = NOTE_NORMAL
    length_us: int = 0           # for LN variants
    sound_id: int = 0            # WAV id
    damage: int = 0              # mine
    # Whether this note is a P2-side note (for SP→DP promotion logic).
    player: int = 1


@dataclass
class Event:
    """A single timeline event used to derive ``length``."""
    time_us: int
    has_visible: bool = False
    has_hidden: bool = False
    has_bgm: bool = False
    has_bga: bool = False


@dataclass
class BMSModel:
    # Header metadata
    title: str = ''
    subtitle: str = ''
    genre: str = ''
    artist: str = ''
    subartist: str = ''
    stagefile: str = ''
    banner: str = ''
    backbmp: str = ''
    preview: str = ''
    playlevel: int = 0
    difficulty: int = 0
    raw_rank: Optional[int] = None
    defexrank: Optional[int] = None
    judge_rank_pct: Optional[int] = None  # BMSON: ``info.judge_rank``
    lntype: int = 1            # #LNTYPE — defaults to 1 LONG
    lnmode: int = 0            # #LNMODE — 0 = unset
    has_lnobj: bool = False
    has_random: bool = False
    has_stop_seq: bool = False
    has_scroll_seq: bool = False

    # Mode-detection helpers
    mode: Mode = BEAT_7K
    used_p1_high_keys: bool = False  # ch 18,19 (1-indexed: keys 6,7+)
    used_p2: bool = False            # any 2x/4x channel hit

    # Notes & timing
    notes: List[Note] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    min_bpm: float = 0.0
    max_bpm: float = 0.0
    initial_bpm: float = 130.0

    # Filesystem-level signal flags (filled by songdata.py).
    has_bmp_defs: bool = False
    has_wav_defs: bool = False
    wav_def_count: int = 0
    bmp_def_count: int = 0
    # Number of judgement-bearing notes that have a WAV assignment — used
    # for the empirical NOKEYSOUND heuristic.
    keysound_count: int = 0
    visible_note_count: int = 0

    def add_note(self, n: Note) -> None:
        self.notes.append(n)

    def record_bpm(self, bpm: float) -> None:
        if bpm <= 0:
            return
        if self.min_bpm == 0.0 or bpm < self.min_bpm:
            self.min_bpm = bpm
        if bpm > self.max_bpm:
            self.max_bpm = bpm
