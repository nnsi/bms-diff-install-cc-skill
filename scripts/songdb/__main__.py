"""CLI entry point.

Clean-room implementation from SPEC.md. No reference to upstream code.

Usage::

    python -m scripts.songdb \\
        --songdata-db PATH \\
        --music-root PATH \\
        --paths FILE [FILE ...]

Or, integrated with the parent ``install_diffs`` skill::

    python -m scripts.songdb \\
        --songdata-db PATH \\
        --music-root PATH \\
        --from-state-dir DIR
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import os
import sys
from typing import Iterable, List

from . import parser_bms, parser_bmson, songdata, writer


CHART_EXTS = ('.bms', '.bme', '.bml', '.pms', '.bmson')


def _parse_one(path: str) -> 'songdata.SongRow':
    ext = os.path.splitext(path)[1].lower()
    if ext == '.bmson':
        model = parser_bmson.parse(path)
    else:
        model = parser_bms.parse(path)
    return songdata.build_song_row(path, model)


def _enumerate_from_state(state_dir: str, music_root: str) -> List[str]:
    """Read state-dir/results.csv from the parent install_diffs run.

    Each row has columns including ``placed`` (basenames of the dropped
    chart files, semicolon-separated when one differential archive
    contains multiple files) and ``top_folder`` (the chosen parent's
    folder name under ``music_root``). Build the full paths and return.
    """
    csv_path = os.path.join(state_dir, 'results.csv')
    out: List[str] = []
    if not os.path.exists(csv_path):
        print(f'[songdb] {csv_path}: not found', file=sys.stderr)
        return out
    with open(csv_path, encoding='utf-8', newline='') as f:
        rd = csv.DictReader(f)
        for r in rd:
            placed = (r.get('placed') or '').strip()
            top = (r.get('top_folder') or '').strip()
            if not placed or not top:
                continue
            for name in placed.split(';'):
                name = name.strip()
                if not name:
                    continue
                full = os.path.join(music_root, top, name)
                if os.path.exists(full):
                    out.append(full)
    return out


def main(argv: list = None) -> int:
    p = argparse.ArgumentParser(prog='python -m scripts.songdb', description=__doc__)
    p.add_argument('--songdata-db', required=True, help='SQLite DB path')
    p.add_argument('--music-root',  required=True, help='Music root directory')
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--paths', nargs='+', help='Chart files to ingest')
    src.add_argument('--from-state-dir',
                     help='Read paths from <dir>/results.csv (parent skill output)')
    p.add_argument('--dry-run', action='store_true',
                   help='Parse only; do not modify the DB')
    p.add_argument('--verbose', '-v', action='store_true')
    args = p.parse_args(argv)

    # Build path list.
    if args.paths:
        paths = list(args.paths)
    else:
        paths = _enumerate_from_state(args.from_state_dir, args.music_root)

    paths = [pp for pp in paths if os.path.splitext(pp)[1].lower() in CHART_EXTS]
    if not paths:
        print('[songdb] no chart paths to process', file=sys.stderr)
        return 1

    rows = []
    for pp in paths:
        try:
            row = _parse_one(pp)
        except Exception as e:
            print(f'[songdb] parse error: {pp}: {e}', file=sys.stderr)
            continue
        rows.append(row)
        if args.verbose:
            print(f'  {pp}: md5={row.md5[:8]} sha={row.sha256[:8]} '
                  f'mode={row.mode} judge={row.judge} '
                  f'feature={row.feature} content={row.content} '
                  f'notes={row.notes} len={row.length}ms')

    if args.dry_run:
        print(f'[songdb] dry-run: parsed {len(rows)} rows, not writing DB')
        return 0

    with contextlib.closing(writer.open_db(args.songdata_db)) as con:
        n = writer.upsert_many(con, rows)
    print(f'[songdb] upserted {n} row(s) into {args.songdata_db}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
