"""Hash helpers.

Clean-room implementation from SPEC.md. No reference to upstream code.

All hashes are standard:

- ``md5_file`` / ``sha256_file`` — RFC 1321 / FIPS 180-4 over the raw file
  bytes. For ``.bmson`` files SPEC mandates ``md5 = ""`` so the caller is
  responsible for routing.
- ``crc32_path`` — standard reflected CRC-32 (poly ``0xEDB88320``) as
  produced by ``binascii.crc32``. The CRC payload follows the contract in
  SPEC §13: ``<absolute-dir>`` + literal ``\\`` (backslash) + ``NUL``.
  Returned as an 8-digit lowercase hex string (the runtime stores it as
  text in the ``song.folder`` / ``song.parent`` columns).
"""

from __future__ import annotations

import binascii
import hashlib
import os


# Read in chunks so multi-hundred-MB BGA-bundled charts do not blow memory.
_CHUNK = 1 << 20


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def crc32_path(directory: str, encoding: str = 'cp932') -> str:
    """Compute the ``folder``/``parent`` CRC for an absolute directory.

    SPEC §13: payload = ``<dir>`` encoded as bytes + b'\\\\' + b'\\0'.

    We pick ``cp932`` by default because that is the JVM default charset
    on Japanese-locale Windows installs (the runtime uses
    ``String.getBytes()`` with no explicit charset). Tests against the
    user's DB confirm ASCII paths match, while non-ASCII paths do **not**
    match for any single Python codec — see SPEC §13 for the sibling
    inheritance workaround.
    """
    # Normalise: strip trailing separators so 'D:/x/' and 'D:/x' agree.
    d = directory.rstrip('\\/').replace('/', '\\')
    try:
        encoded = d.encode(encoding)
    except UnicodeEncodeError:
        # Fall back to lossy cp932 to avoid raising; this row will be
        # rewritten by sibling inheritance anyway when a sibling exists.
        encoded = d.encode(encoding, errors='replace')
    payload = encoded + b'\\' + b'\x00'
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return f'{crc:08x}'
