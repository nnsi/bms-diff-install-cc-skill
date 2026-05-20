"""BMS / BME / BML / PMS parser.

Clean-room implementation from SPEC.md. No reference to upstream code.

The parser follows the public BMS specification (channel list & header
commands), see SPEC.md §"BMS file format" for the summary.

Output: a :class:`BMSModel` ready for projection to a SongRow.
"""

from __future__ import annotations

import os
import random
import re
from typing import Dict, List, Optional, Tuple

from .model import (
    BMSModel, Event, Note,
    NOTE_NORMAL, NOTE_LONG, NOTE_CHARGE, NOTE_HELL,
    NOTE_MINE, NOTE_HIDDEN, NOTE_LONG_END,
)
from .mode import (
    BEAT_5K, BEAT_7K, BEAT_10K, BEAT_14K,
    POPN_9K, KEYBOARD_24K, KEYBOARD_24K_DP,
)


# Constants tied to the public BMS spec --------------------------------------
US_PER_BEAT_AT_BPM_60 = 1_000_000  # microseconds per quarter note at 60 BPM
BAR_BEATS = 4.0                    # default 4/4

CHART_RE = re.compile(r'^#(\d{3})([0-9A-Za-z]{2}):(.*)$')
HEADER_RE = re.compile(r'^#([A-Za-z][A-Za-z0-9]*)\s*(.*)$')


def _decode_id(s: str, base: int = 36) -> int:
    s = s.strip()
    if not s or s == '00':
        return 0
    try:
        return int(s, base)
    except ValueError:
        return 0


def _split_pairs(data: str) -> List[str]:
    data = data.strip()
    # Each entry is exactly 2 chars; truncate odd-length leftovers.
    n = (len(data) // 2) * 2
    return [data[i:i+2] for i in range(0, n, 2)]


# ---------------------------------------------------------------------------
# Pre-pass: decode bytes → list of effective lines (random branch resolved).
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str:
    with open(path, 'rb') as f:
        data = f.read()
    # Strip UTF-8 BOM if present (some authors save in UTF-8).
    if data.startswith(b'\xef\xbb\xbf'):
        try:
            return data[3:].decode('utf-8', errors='replace')
        except Exception:
            pass
    # Default encoding is cp932 (Shift-JIS on Windows).
    try:
        return data.decode('cp932', errors='replace')
    except LookupError:
        return data.decode('shift_jis', errors='replace')


def _resolve_random(lines: List[str], rng: random.Random) -> List[str]:
    """Resolve #RANDOM / #IF / #ENDIF / #ENDRANDOM nesting.

    Returns a flat list of lines from the active branches.
    """
    out: List[str] = []
    # Stack of (in_random, chosen_value, active_truth, seen_if_match)
    # We model each random scope with its drawn value; the IF/ENDIF inside
    # selects activity.
    stack: List[Tuple[int, bool]] = []  # (chosen, currently_emitting)
    emit = True
    for line in lines:
        s = line.strip()
        u = s.upper()
        if u.startswith('#RANDOM'):
            parts = s.split()
            try:
                hi = int(parts[1])
            except Exception:
                hi = 1
            chosen = rng.randint(1, max(1, hi))
            stack.append((chosen, emit))
            continue
        if u.startswith('#SETRANDOM'):
            parts = s.split()
            try:
                chosen = int(parts[1])
            except Exception:
                chosen = 1
            stack.append((chosen, emit))
            continue
        if u.startswith('#ENDRANDOM'):
            if stack:
                _, emit_outer = stack.pop()
                emit = emit_outer
            continue
        if u.startswith('#IF'):
            parts = s.split()
            try:
                target = int(parts[1])
            except Exception:
                target = -1
            if stack:
                chosen, emit_outer = stack[-1]
                emit = emit_outer and (target == chosen)
            else:
                emit = False
            continue
        if u.startswith('#ELSEIF'):
            parts = s.split()
            try:
                target = int(parts[1])
            except Exception:
                target = -1
            if stack:
                chosen, emit_outer = stack[-1]
                emit = emit_outer and (target == chosen)
            continue
        if u.startswith('#ELSE'):
            # Restore IF state (this is a simplification; nested #IF blocks
            # are rare in real charts).
            continue
        if u.startswith('#ENDIF'):
            if stack:
                _, emit_outer = stack[-1]
                emit = emit_outer
            continue
        if emit:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse(path: str, *, seed: Optional[int] = None) -> BMSModel:
    """Parse a BMS-family file into a :class:`BMSModel`.

    ``seed`` controls #RANDOM resolution determinism for reproducible
    golden tests.
    """
    text = _read_text(path)
    rng = random.Random(seed) if seed is not None else random.Random()
    raw_lines = text.splitlines()
    has_random = any(l.strip().upper().startswith('#RANDOM') for l in raw_lines)
    lines = _resolve_random(raw_lines, rng) if has_random else raw_lines

    m = BMSModel()
    m.has_random = has_random

    # Default mode by extension.
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pms':
        m.mode = POPN_9K
    else:
        m.mode = BEAT_7K  # default per SPEC; will be downgraded to 5K below

    base = 36
    lnobj_id = 0
    # #WAVxx / #BMPxx / #BPMxx / #STOPxx / #SCROLLxx tables.
    bpm_tab: Dict[int, float] = {}
    stop_tab: Dict[int, int] = {}
    scroll_tab: Dict[int, float] = {}
    wav_defs: Dict[int, str] = {}
    bmp_defs: Dict[int, str] = {}

    # Chart lines accumulated by bar.
    chart_lines: List[Tuple[int, str, str]] = []   # (bar, channel, data)

    # ---------- header pass ----------
    for line in lines:
        s = line.strip()
        if not s.startswith('#'):
            continue
        # Chart-line detection takes priority over header parsing.
        cm = CHART_RE.match(s)
        if cm:
            bar = int(cm.group(1))
            ch  = cm.group(2).upper()
            chart_lines.append((bar, ch, cm.group(3)))
            continue
        hm = HEADER_RE.match(s)
        if not hm:
            continue
        key = hm.group(1).upper()
        val = hm.group(2).strip()
        if not val:
            continue
        # Per-ID definitions (key has 2-char suffix after the command).
        if key.startswith('WAV') and len(key) == 5:
            wav_defs[_decode_id(key[3:], base)] = val
            continue
        if key.startswith('BMP') and len(key) == 5:
            bmp_defs[_decode_id(key[3:], base)] = val
            continue
        if key.startswith('BPM') and len(key) == 5:
            try: bpm_tab[_decode_id(key[3:], base)] = float(val)
            except ValueError: pass
            continue
        if key.startswith('STOP') and len(key) == 6:
            try: stop_tab[_decode_id(key[4:], base)] = int(val)
            except ValueError: pass
            continue
        if key.startswith('SCROLL') and len(key) == 8:
            try: scroll_tab[_decode_id(key[6:], base)] = float(val)
            except ValueError: pass
            continue

        # Scalar headers
        if key == 'TITLE': m.title = val
        elif key == 'SUBTITLE': m.subtitle = val
        elif key == 'ARTIST': m.artist = val
        elif key == 'SUBARTIST': m.subartist = val
        elif key == 'GENRE': m.genre = val
        elif key == 'STAGEFILE': m.stagefile = val
        elif key == 'BANNER': m.banner = val
        elif key == 'BACKBMP': m.backbmp = val
        elif key == 'PREVIEW': m.preview = val
        elif key == 'PLAYLEVEL':
            try: m.playlevel = int(float(val))
            except ValueError: pass
        elif key == 'DIFFICULTY':
            try: m.difficulty = int(val)
            except ValueError: pass
        elif key == 'RANK':
            try: m.raw_rank = int(val)
            except ValueError: pass
        elif key == 'DEFEXRANK':
            try: m.defexrank = int(val)
            except ValueError: pass
        elif key == 'BPM':
            try: m.initial_bpm = float(val); m.record_bpm(m.initial_bpm)
            except ValueError: pass
        elif key == 'LNTYPE':
            try: m.lntype = int(val)
            except ValueError: pass
        elif key == 'LNMODE':
            try: m.lnmode = int(val)
            except ValueError: pass
        elif key == 'LNOBJ':
            lnobj_id = _decode_id(val, base)
            m.has_lnobj = True
        elif key == 'BASE':
            try:
                b = int(val)
                if b in (36, 62): base = b
            except ValueError: pass
        elif key == 'PLAYER':
            # 2 / 3 means multi/DP; we'll let used_p2 promote modes.
            pass

    m.has_stop_seq = bool(stop_tab)
    m.has_scroll_seq = bool(scroll_tab)
    m.wav_def_count = len(wav_defs)
    m.bmp_def_count = len(bmp_defs)
    m.has_wav_defs = bool(wav_defs)
    m.has_bmp_defs = bool(bmp_defs)

    # ---------- chart pass: build timeline ----------
    # We compute event positions as (bar_index + fractional_position).
    # Section length multipliers from channel 02 scale a single bar.
    section_rate: Dict[int, float] = {}

    # First sweep: pull out section-rate definitions so they apply when we
    # iterate the rest.
    chart_by_bar: Dict[int, List[Tuple[str, str]]] = {}
    for bar, ch, data in chart_lines:
        if ch == '02':
            try: section_rate[bar] = float(data)
            except ValueError: pass
            continue
        chart_by_bar.setdefault(bar, []).append((ch, data))

    # Compute cumulative bar start times (microseconds) using current BPM at
    # bar transitions. We need BPM changes inside a bar handled too — do it
    # in a per-bar pass after we sort events.
    max_bar = max(chart_by_bar.keys() | section_rate.keys() | {0})

    cur_us = 0
    cur_bpm = m.initial_bpm or 130.0
    m.record_bpm(cur_bpm)

    # Used-channel tracking for mode detection.
    p1_keys_seen = set()
    p2_keys_seen = set()

    # LN tracking — per-lane "currently open LN" state machine. For ch 5x/6x
    # the BMS spec uses pair toggles: first non-zero entry opens an LN, the
    # next non-zero entry on the same lane closes it.
    ln_open: Dict[int, Note] = {}  # lane -> note

    for bar in range(max_bar + 1):
        rate = section_rate.get(bar, 1.0)
        bar_entries = chart_by_bar.get(bar, [])
        # Collect (rel_pos, channel, id_int, raw_id) for ordering.
        events: List[Tuple[float, str, int, str]] = []
        for ch, data in bar_entries:
            ids = _split_pairs(data)
            if not ids:
                continue
            n = len(ids)
            for i, idstr in enumerate(ids):
                if idstr == '00':
                    continue
                pos = i / n  # fractional position within the bar
                val = _decode_id(idstr, base)
                events.append((pos, ch, val, idstr))
        events.sort(key=lambda e: (e[0], e[1]))

        # Bar duration in microseconds at cur_bpm.
        def _bar_us(bpm: float) -> float:
            return (60_000_000.0 * BAR_BEATS) / bpm * rate

        # We process events in order, advancing cur_us by per-event delta
        # in microseconds. This properly handles BPM changes mid-bar.
        prev_pos = 0.0
        bar_us_at_start_bpm = _bar_us(cur_bpm)
        last_event_us = cur_us
        for (pos, ch, val, idstr) in events:
            # Advance time from prev_pos → pos at cur_bpm.
            dt_us = (pos - prev_pos) * _bar_us(cur_bpm)
            event_us = cur_us + int(dt_us)
            prev_pos = pos
            cur_us = event_us

            ev = Event(time_us=event_us)
            # Dispatch by channel ------------------------------------------
            if ch == '01':
                ev.has_bgm = True
            elif ch == '03':
                # BPM change direct: id is a hex byte (00-FF) representing
                # the BPM as integer.
                try:
                    new_bpm = float(int(idstr, 16))
                    if new_bpm > 0:
                        cur_bpm = new_bpm
                        m.record_bpm(cur_bpm)
                except ValueError:
                    pass
            elif ch == '04' or ch == '07' or ch == '06':
                ev.has_bga = True
            elif ch == '08':
                if val in bpm_tab:
                    cur_bpm = bpm_tab[val]
                    m.record_bpm(cur_bpm)
            elif ch == '09':
                # STOP: T units of 1/192 of a 4/4 bar (per public BMS spec).
                if val in stop_tab:
                    t = stop_tab[val]
                    cur_us += int(t / 192.0 * (60_000_000.0 * BAR_BEATS) / cur_bpm)
            elif ch == 'SC':
                pass  # scroll change — timing-neutral
            elif ch[0] in ('1', '2', '3', '4', '5', '6', 'D', 'E') and ch[1] in '123456789':
                # Lane-bearing channels.
                first = ch[0]
                lane_digit = int(ch[1])
                lane = lane_digit  # 1..9
                player = 1
                if first in ('1', '3', '5', 'D'):
                    player = 1
                else:
                    player = 2
                if player == 1:
                    p1_keys_seen.add(lane_digit)
                else:
                    p2_keys_seen.add(lane_digit)

                # Note kind classification.
                kind = NOTE_NORMAL
                if first in ('1', '2'):  # visible
                    # LNOBJ end check.
                    if m.has_lnobj and val == lnobj_id:
                        # The previous visible-note on this lane becomes an LN.
                        kind = NOTE_LONG_END
                        # Promote previous note on this lane (search back).
                        for prev_note in reversed(m.notes):
                            if prev_note.lane == lane and prev_note.player == player and prev_note.kind == NOTE_NORMAL:
                                # Set LN type using LNMODE if specified.
                                if m.lnmode == 2: prev_note.kind = NOTE_CHARGE
                                elif m.lnmode == 3: prev_note.kind = NOTE_HELL
                                else: prev_note.kind = NOTE_LONG
                                prev_note.length_us = event_us - prev_note.time_us
                                break
                        # The LNOBJ slot itself does not contribute to notes.
                        ev.has_visible = True
                        m.events.append(ev)
                        continue
                    kind = NOTE_NORMAL
                    ev.has_visible = True
                elif first in ('3', '4'):  # hidden / invisible
                    kind = NOTE_HIDDEN
                    ev.has_hidden = True
                elif first in ('5', '6'):  # LN channel
                    # LN toggle.
                    key = (player, lane)
                    if key in ln_open:
                        prev = ln_open.pop(key)
                        prev.length_us = event_us - prev.time_us
                        ev.has_visible = True
                        m.events.append(ev)
                        continue
                    # Open new LN.
                    if m.lntype == 2 or m.lnmode == 2:
                        kind = NOTE_CHARGE
                    elif m.lntype == 3 or m.lnmode == 3:
                        kind = NOTE_HELL
                    else:
                        kind = NOTE_LONG
                    ev.has_visible = True
                elif first in ('D', 'E'):  # mine
                    kind = NOTE_MINE
                    ev.has_visible = True

                note = Note(lane=lane, time_us=event_us, kind=kind, sound_id=val, player=player)
                if kind in (NOTE_LONG, NOTE_CHARGE, NOTE_HELL):
                    ln_open[(player, lane)] = note
                if kind == NOTE_MINE:
                    note.damage = val
                m.add_note(note)
            # ----------------------------------------------------------------
            m.events.append(ev)

        # Advance cur_us to end of bar at current BPM.
        remaining = (1.0 - prev_pos) * _bar_us(cur_bpm)
        cur_us = cur_us + int(remaining) if events else cur_us + int(_bar_us(cur_bpm))

    # Mode detection.
    m.used_p2 = bool(p2_keys_seen)
    m.used_p1_high_keys = any(d in p1_keys_seen for d in (8,))
    # Channel layout: standard BMS uses
    #   key1=11, key2=12, ..., key5=15, sc=16, key6=18, key7=19
    # So lanes 8 and 9 = keys 6 and 7 (7K markers). Lane 6 = scratch.
    has_7k = any(d in p1_keys_seen for d in (8, 9))
    if ext == '.pms':
        m.mode = POPN_9K
    else:
        if m.used_p2:
            m.mode = BEAT_14K if has_7k else BEAT_10K
        else:
            m.mode = BEAT_7K if has_7k else BEAT_5K

    # Bookkeeping counters used by songdata.py.
    m.visible_note_count = sum(
        1 for n in m.notes
        if n.kind in (NOTE_NORMAL, NOTE_LONG, NOTE_CHARGE, NOTE_HELL)
    )
    m.keysound_count = sum(
        1 for n in m.notes
        if n.sound_id != 0
        and n.kind in (NOTE_NORMAL, NOTE_LONG, NOTE_CHARGE, NOTE_HELL)
    )

    if m.min_bpm == 0.0:
        m.min_bpm = m.initial_bpm or 130.0
    if m.max_bpm == 0.0:
        m.max_bpm = m.initial_bpm or 130.0
    return m
