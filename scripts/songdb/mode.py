"""Mode constants.

Clean-room implementation from SPEC.md. No reference to upstream code.

The ID column comes from the SPEC §"Mode IDs (observed)" table, verified
against ``SELECT DISTINCT mode FROM song`` on the user's real DB.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    id: int
    hint: str   # BMSON ``info.mode_hint`` string, lowercase
    keys: int   # key count per player
    players: int


BEAT_5K  = Mode(5,  'beat-5k',             6,  1)
BEAT_7K  = Mode(7,  'beat-7k',             8,  1)
BEAT_10K = Mode(10, 'beat-10k',            12, 2)
BEAT_14K = Mode(14, 'beat-14k',            16, 2)
POPN_9K  = Mode(9,  'popn-9k',             9,  1)
POPN_5K  = Mode(9,  'popn-5k',             5,  1)
KEYBOARD_24K = Mode(25, 'keyboard-24k',          26, 1)
KEYBOARD_24K_DP = Mode(50, 'keyboard-24k-double', 52, 2)


# All known modes (ordering preserved for predictable iteration).
ALL_MODES = (
    BEAT_5K, BEAT_7K, BEAT_10K, BEAT_14K,
    POPN_9K, POPN_5K,
    KEYBOARD_24K, KEYBOARD_24K_DP,
)


def by_hint(hint: str) -> Mode:
    """Resolve a BMSON ``mode_hint`` string to a Mode.

    SPEC §"BMSON file format": unknown hint → fall back to BEAT_7K.
    """
    if not hint:
        return BEAT_7K
    h = hint.strip().lower()
    for m in ALL_MODES:
        if m.hint == h:
            return m
    return BEAT_7K
