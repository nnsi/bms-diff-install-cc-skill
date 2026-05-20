"""Golden test harness.

Clean-room implementation from SPEC.md. No reference to upstream code.

Compares the rows our pipeline would produce against the live
``songdata.db``. Reports per-column match rates so we can verify the
empirical transforms (judge, feature/content bits, length/notes).

Usage::

    python -m scripts.songdb._golden_test \\
        --songdata-db D:/bms/beatoraja/songdata.db \\
        --music-root  D:/bms/music \\
        --sample 200
"""

from __future__ import annotations

import argparse
import contextlib
import os
import random
import sqlite3
import sys
import traceback
from collections import Counter
from typing import Dict, List, Tuple

from . import parser_bms, parser_bmson, songdata, writer


# Columns we compare, paired with their match-rate targets from SPEC.
COMPARED = [
    ('md5',1.00), ('sha256',1.00),
    ('title',0.99), ('artist',0.99), ('genre',0.99),
    ('subtitle',0.99), ('subartist',0.99),
    ('level',0.95), ('difficulty',0.95), ('mode',0.95),
    ('notes',0.95), ('length',0.95),
    ('maxbpm',0.95), ('minbpm',0.95),
    ('judge',0.95),
    ('feature',0.90), ('content',0.90),
    ('folder_writer',1.00),    # post-sibling-inheritance comparison
    ('parent_writer',1.00),
    ('charthash',0.0),
]


def _decode(b):
    if b is None: return ''
    if isinstance(b, bytes):
        # Beatoraja stores all text columns (including `path`) as UTF-8.
        try:
            return b.decode('utf-8')
        except UnicodeDecodeError:
            return b.decode('cp932', errors='replace')
    return b


def _approx_equal(col: str, ours, theirs) -> bool:
    if col in ('length','minbpm','maxbpm','notes'):
        try:
            a, b = int(ours), int(theirs)
        except (TypeError, ValueError):
            return ours == theirs
        # Allow small tolerance for length/notes due to floating-point bar walks.
        if col == 'length':
            return abs(a - b) <= max(1000, b // 50)
        if col == 'notes':
            return abs(a - b) <= max(2, b // 100)
        return abs(a - b) <= 1
    if col in ('feature','content','mode','level','difficulty','judge'):
        try:
            return int(ours) == int(theirs)
        except (TypeError, ValueError):
            return ours == theirs
    if col == 'charthash':
        return ours == theirs
    # String-y columns — strip / compare loosely.
    a = (_decode(ours) if not isinstance(ours, str) else ours).strip()
    b = (_decode(theirs) if not isinstance(theirs, str) else theirs).strip()
    return a == b


def _sample_rows(db_path: str, n: int, seed: int = 99):
    con = sqlite3.connect(db_path)
    con.text_factory = bytes
    cur = con.cursor()
    cur.execute("SELECT * FROM song")
    col_names = [c[0] for c in cur.description]
    rows = cur.fetchall()
    con.close()
    rng = random.Random(seed)
    rng.shuffle(rows)
    sample = []
    for r in rows:
        d = {col_names[i]: r[i] for i in range(len(col_names))}
        try:
            p = d['path'].decode('utf-8') if isinstance(d['path'], bytes) else d['path']
        except UnicodeDecodeError:
            continue
        if not p or not os.path.exists(p):
            continue
        sample.append(d)
        if len(sample) >= n:
            break
    return sample


def _build_ours(path: str) -> 'songdata.SongRow':
    ext = os.path.splitext(path)[1].lower()
    if ext == '.bmson':
        model = parser_bmson.parse(path)
    else:
        model = parser_bms.parse(path, seed=0)
    return songdata.build_song_row(path, model)


def _coerce_db(col: str, value):
    if value is None:
        return '' if col in ('md5','sha256','title','subtitle','genre','artist',
                              'subartist','tag','path','folder','stagefile',
                              'banner','backbmp','preview','parent','charthash') else 0
    return value


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--songdata-db', required=True)
    p.add_argument('--music-root',  required=True)
    p.add_argument('--sample', type=int, default=200)
    p.add_argument('--seed', type=int, default=99)
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--show-mismatches', type=int, default=5,
                   help='print up to N mismatch examples per column')
    args = p.parse_args(argv)

    # Windows consoles default to cp932 — force utf-8 for diagnostic prints
    # so non-cp932 characters in titles / paths don't crash the harness.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

    sample = _sample_rows(args.songdata_db, args.sample, args.seed)
    print(f'[golden] sampled {len(sample)} rows that exist on disk')

    # Pre-open a connection for sibling lookups (writer.upsert path).
    con = sqlite3.connect(args.songdata_db)
    con.text_factory = bytes

    counts = Counter()
    matches = Counter()
    examples: Dict[str, List[Tuple[str, object, object]]] = {c[0]: [] for c in COMPARED}
    parse_failures = 0

    for i, db_row in enumerate(sample):
        path = db_row['path'].decode('utf-8') if isinstance(db_row['path'], bytes) else db_row['path']
        try:
            our_row = _build_ours(path)
        except Exception as e:
            parse_failures += 1
            if args.verbose:
                print(f'  parse fail: {path}: {e}')
                traceback.print_exc()
            continue

        # Apply sibling inheritance to derive folder_writer / parent_writer.
        sib = writer._lookup_sibling(con, os.path.dirname(path))
        if sib:
            folder_w, parent_w = sib
        else:
            folder_w, parent_w = our_row.folder, our_row.parent

        for col, _target in COMPARED:
            counts[col] += 1
            if col == 'folder_writer':
                ours = folder_w
                theirs = _decode(db_row['folder'])
            elif col == 'parent_writer':
                ours = parent_w
                theirs = _decode(db_row['parent'])
            else:
                ours = getattr(our_row, col)
                theirs = _coerce_db(col, db_row.get(col))
            if _approx_equal(col, ours, theirs):
                matches[col] += 1
            else:
                if len(examples[col]) < args.show_mismatches:
                    examples[col].append((path, ours, theirs))

    print()
    print(f'{"column":<18s} {"match":>6s} {"total":>6s} {"rate":>7s} {"target":>7s}  ok?')
    ok_all = True
    for col, target in COMPARED:
        c = counts[col] or 1
        m = matches[col]
        rate = m / c
        ok = rate >= target
        if not ok and target > 0:
            ok_all = False
        flag = 'OK ' if ok else '!! '
        print(f'{col:<18s} {m:>6d} {c:>6d} {rate*100:>6.1f}% {target*100:>6.1f}%  {flag}')

    if args.verbose or args.show_mismatches:
        print()
        for col, _ in COMPARED:
            ex = examples[col]
            if not ex:
                continue
            print(f'--- {col} mismatches (first {len(ex)}) ---')
            for path, ours, theirs in ex:
                if isinstance(theirs, (bytes, bytearray)):
                    try: theirs_s = theirs.decode('cp932')
                    except UnicodeDecodeError: theirs_s = repr(theirs)
                else:
                    theirs_s = repr(theirs)
                if isinstance(ours, (bytes, bytearray)):
                    try: ours_s = ours.decode('cp932')
                    except UnicodeDecodeError: ours_s = repr(ours)
                else:
                    ours_s = repr(ours)
                print(f'  {path}\n    ours={ours_s!s:.120s}\n    db  ={theirs_s!s:.120s}')

    print()
    print(f'[golden] parse_failures={parse_failures}')
    con.close()
    return 0 if ok_all else 2


if __name__ == '__main__':
    sys.exit(main())
