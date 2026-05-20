"""songdb — clean-room reimplementation from SPEC.md.

No reference to upstream beatoraja / jbms-parser code was consulted.
All design follows the contract in ``SPEC.md`` and behaviors derived
empirically from the user's own ``songdata.db``.
"""

__all__ = [
    'hashing', 'mode', 'model', 'parser_bms', 'parser_bmson',
    'songdata', 'writer',
]
