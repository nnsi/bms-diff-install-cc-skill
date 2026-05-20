"""BMSON parser.

Clean-room implementation from SPEC.md. No reference to upstream code.

Follows the public BMSON spec at https://bmson-spec.readthedocs.io/.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .model import (
    BMSModel, Note,
    NOTE_NORMAL, NOTE_LONG, NOTE_CHARGE, NOTE_HELL,
    NOTE_MINE, NOTE_HIDDEN,
)
from .mode import by_hint, BEAT_7K, POPN_9K


def _read_json(path: str):
    with open(path, 'rb') as f:
        data = f.read()
    if data.startswith(b'\xef\xbb\xbf'):
        data = data[3:]
    return json.loads(data.decode('utf-8', errors='replace'))


def parse(path: str) -> BMSModel:
    obj = _read_json(path)
    m = BMSModel()

    info = obj.get('info') or {}
    m.title = str(info.get('title', '') or '')
    m.subtitle = str(info.get('subtitle', '') or '')
    m.artist = str(info.get('artist', '') or '')
    m.genre = str(info.get('genre', '') or '')
    # ``subartists`` is a JSON array per the BMSON spec.
    sub = info.get('subartists') or info.get('subartist') or []
    if isinstance(sub, list):
        m.subartist = ', '.join(str(x) for x in sub if x)
    else:
        m.subartist = str(sub)
    m.stagefile = str(info.get('eyecatch_image', '') or '')
    m.banner = str(info.get('banner_image', '') or '')
    m.backbmp = str(info.get('back_image', '') or '')
    m.preview = str(info.get('preview_music', '') or '')

    # Chart name → append to subtitle in [chart_name] form (SPEC).
    chart_name = info.get('chart_name')
    if chart_name:
        suffix = '[' + str(chart_name) + ']'
        if m.subtitle:
            m.subtitle = m.subtitle + ' ' + suffix
        else:
            m.subtitle = suffix

    try:
        m.playlevel = int(info.get('level', 0) or 0)
    except (TypeError, ValueError):
        m.playlevel = 0

    try:
        m.judge_rank_pct = int(info.get('judge_rank', 0) or 0)
    except (TypeError, ValueError):
        m.judge_rank_pct = None

    # Mode resolution.
    mode_hint = info.get('mode_hint', '')
    m.mode = by_hint(str(mode_hint)) if mode_hint else BEAT_7K
    # PMS-style BMSON files use popn-9k by default if hint missing.
    if not mode_hint and path.lower().endswith('.pms.bmson'):
        m.mode = POPN_9K

    try:
        m.initial_bpm = float(info.get('init_bpm', 130.0) or 130.0)
    except (TypeError, ValueError):
        m.initial_bpm = 130.0
    m.record_bpm(m.initial_bpm)

    resolution = int(info.get('resolution', 240) or 240)
    if resolution <= 0:
        resolution = 240

    try:
        m.lntype = int(info.get('ln_type', 0) or 0)
    except (TypeError, ValueError):
        m.lntype = 0

    # ---- Build timeline so we can compute time_us for notes. ----
    # The unit on ``y`` is "pulses"; ``resolution`` pulses = 1 quarter note.
    bpm_events = obj.get('bpm_events') or []
    stop_events = obj.get('stop_events') or []
    scroll_events = obj.get('scroll_events') or []
    m.has_stop_seq = bool(stop_events)
    m.has_scroll_seq = bool(scroll_events)

    # Sort by ``y`` and convert to absolute microseconds.
    bpm_events = sorted(bpm_events, key=lambda e: e.get('y', 0))
    stop_events = sorted(stop_events, key=lambda e: e.get('y', 0))
    # Merge bpm + stop into a single event stream for time walking.
    timeline_events = []
    for e in bpm_events:
        timeline_events.append((int(e.get('y', 0)), 'bpm', float(e.get('bpm', m.initial_bpm))))
    for e in stop_events:
        timeline_events.append((int(e.get('y', 0)), 'stop', int(e.get('duration', 0))))
    timeline_events.sort(key=lambda t: (t[0], 0 if t[1] == 'bpm' else 1))

    def y_to_us(y: int) -> int:
        cur_us = 0.0
        cur_bpm = m.initial_bpm
        cur_y = 0
        for (ey, etype, eval_) in timeline_events:
            if ey >= y:
                break
            # advance to ey
            dy = ey - cur_y
            cur_us += dy * (60_000_000.0 / (cur_bpm * resolution))
            cur_y = ey
            if etype == 'bpm':
                if eval_ > 0:
                    cur_bpm = eval_
                    m.record_bpm(cur_bpm)
            elif etype == 'stop':
                cur_us += eval_ * (60_000_000.0 / (cur_bpm * resolution))
        cur_us += (y - cur_y) * (60_000_000.0 / (cur_bpm * resolution))
        return int(cur_us)

    # Pre-record BPMs from event list.
    for (_y, etype, val) in timeline_events:
        if etype == 'bpm' and val > 0:
            m.record_bpm(val)

    # Process note channels.
    channels = obj.get('sound_channels') or []
    for ch in channels:
        for n in ch.get('notes') or []:
            x = n.get('x') or 0
            try:
                lane = int(x)
            except (TypeError, ValueError):
                lane = 0
            if lane <= 0:
                # x == 0 / null means BGM (no judge).
                continue
            y = int(n.get('y', 0))
            length = int(n.get('l', 0) or 0)
            t_override = int(n.get('t', 0) or 0)
            t_us = y_to_us(y)
            if length > 0:
                # Determine LN kind.
                t = t_override or m.lntype
                if t == 2:
                    kind = NOTE_CHARGE
                elif t == 3:
                    kind = NOTE_HELL
                else:
                    kind = NOTE_LONG
                length_us = y_to_us(y + length) - t_us
            else:
                kind = NOTE_NORMAL
                length_us = 0
            m.add_note(Note(
                lane=lane, time_us=t_us, kind=kind, length_us=length_us,
                player=1,
            ))

    # Mines.
    for ch in obj.get('mine_channels') or []:
        for n in ch.get('notes') or []:
            x = n.get('x') or 0
            try:
                lane = int(x)
            except (TypeError, ValueError):
                lane = 0
            if lane <= 0:
                continue
            y = int(n.get('y', 0))
            t_us = y_to_us(y)
            m.add_note(Note(
                lane=lane, time_us=t_us, kind=NOTE_MINE,
                damage=int(n.get('damage', 0) or 0), player=1,
            ))

    # Hidden / keysounds (no judge contribution).
    for ch in obj.get('key_channels') or []:
        for n in ch.get('notes') or []:
            x = n.get('x') or 0
            try:
                lane = int(x)
            except (TypeError, ValueError):
                lane = 0
            if lane <= 0:
                continue
            y = int(n.get('y', 0))
            t_us = y_to_us(y)
            m.add_note(Note(
                lane=lane, time_us=t_us, kind=NOTE_HIDDEN, player=1,
            ))

    # Compute total length from last note position.
    # Empirically (verified against the live DB), the BMSON `length`
    # column reflects the end of the last note, not the chart's bar
    # extent — silent tail-bars are excluded.
    last_y = 0
    for ch in channels:
        for n in ch.get('notes') or []:
            try:
                ny = int(n.get('y', 0)) + int(n.get('l', 0) or 0)
                last_y = max(last_y, ny)
            except Exception:
                pass
    for ch in obj.get('mine_channels') or []:
        for n in ch.get('notes') or []:
            try:
                ny = int(n.get('y', 0))
                last_y = max(last_y, ny)
            except Exception:
                pass

    # Stash last event time as a single synthetic Event in m.events so the
    # length calculator in songdata.py can pick it up.
    from .model import Event
    m.events.append(Event(time_us=y_to_us(last_y)))

    # Counters.
    from .model import NOTE_NORMAL as _NN, NOTE_LONG as _NL, NOTE_CHARGE as _NC, NOTE_HELL as _NH
    m.visible_note_count = sum(
        1 for nn in m.notes if nn.kind in (_NN, _NL, _NC, _NH)
    )
    # BMSON files: keysound count is approximated by visible-note count
    # because each note in ``sound_channels`` has its own waveform.
    m.keysound_count = m.visible_note_count
    m.has_wav_defs = bool(channels)
    # Approximate WAV def count from BMSON sound_channels — each entry
    # represents one playable waveform, equivalent to a #WAVxx slot.
    m.wav_def_count = len(channels)
    bga = obj.get('bga') or {}
    bmp_defs = bga.get('bga_header') if isinstance(bga, dict) else None
    m.has_bmp_defs = bool(bmp_defs)
    if bmp_defs:
        m.bmp_def_count = len(bmp_defs) if hasattr(bmp_defs, '__len__') else 0

    if m.min_bpm == 0.0:
        m.min_bpm = m.initial_bpm
    if m.max_bpm == 0.0:
        m.max_bpm = m.initial_bpm
    return m
