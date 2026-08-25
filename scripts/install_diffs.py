"""
Install BMS differential charts from a beatoraja-compatible difficulty table.

Pipeline per entry:
  1. Skip if md5 already in songdata.db.
  1b. If the entry has no usable url_diff, the差分 ships inside the parent
     package instead of as its own download. Record it as 'bundled_in_parent'
     in no_parent.jsonl so install_parents.py fetches the package (which
     carries the chart) — do NOT drop it silently.
  2. Download url_diff (GDrive /file/d/{ID}/ URLs are auto-normalized; folder
     URLs need manual handling — they get logged as errors).
  3. Extract chart files (.bms/.bme/.bml/.pms/.bmson) and BGA assets from the
     archive — zip in-process, rar/7z/lzh via 7-Zip — or treat the body as a
     raw BMS if it is not an archive. When the archive is a bundle carrying
     差分 for many songs, only the directory holding this entry's own md5 is
     kept, so unrelated charts are not poured into the parent folder.
  4. Parse first chart's #WAV refs and score every music folder by hit count.
  5. Decide auto / ambiguous / no_parent based on hit ratio and gap to 2nd.
  6. In --apply mode, copy chart + BGA files into the chosen parent folder
     (never overwriting existing files).

Output state files:
  <state-dir>/results.csv      one row per processed entry
  <state-dir>/ambiguous.jsonl  cases needing model review
  <state-dir>/no_parent.jsonl  parent song apparently not installed
  <state-dir>/errors.jsonl     download/parse errors
  <state-dir>/downloads/       cached url_diff bodies (keyed by md5)
  <state-dir>/header.json      cached table header (re-fetched each run unless
                               --no-refresh-table)
  <state-dir>/data.json        cached table body (likewise)
"""

import argparse, csv, hashlib, io, json, os, re, sqlite3, sys, tempfile, unicodedata
import time, traceback, zipfile
import urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from threading import Lock

try:                     # imported as part of the `scripts` package (run_gui.py)
    from . import install_parents
except ImportError:      # run directly: python scripts/install_diffs.py
    import install_parents

CHART_EXT  = ('.bms','.bme','.bml','.pms','.bmson')
BGA_EXT    = ('.bmp','.png','.jpg','.jpeg','.gif','.wmv','.mp4','.avi','.mov','.webm','.mpg','.mpeg')
AUDIO_EXT  = ('.wav','.ogg','.mp3','.flac','.m4a','.opus')

AUTO_RATIO = 0.95
AUTO_GAP   = 0.20
SKIP_RATIO = 0.50
WAV_SAMPLE_MAX = 400

DL_WORKERS = 5
DL_TIMEOUT = 60
DL_RETRY   = 2
UA = 'Mozilla/5.0 (BMS diff installer)'


def normalize_url(url):
    m = re.match(r'https?://drive\.google\.com/file/d/([^/]+)', url)
    if m:
        return f'https://drive.google.com/uc?export=download&id={m.group(1)}'
    m = re.match(r'https?://drive\.google\.com/open\?id=([^&]+)', url)
    if m:
        return f'https://drive.google.com/uc?export=download&id={m.group(1)}'
    return url


def fetch(url, retry=DL_RETRY, timeout=DL_TIMEOUT):
    url = normalize_url(url)
    last = None
    for i in range(retry+1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get('Content-Type','')
        except Exception as e:
            last = e
            time.sleep(1 + i)
    raise last


def installed_md5_set(songdata_db):
    con = sqlite3.connect(songdata_db)
    con.text_factory = bytes
    cur = con.cursor()
    cur.execute("SELECT md5 FROM song")
    s = {r[0].decode('ascii') for r in cur.fetchall()}
    con.close()
    return s


def build_audio_index(root):
    idx = {}
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder): continue
        try:
            audios = set()
            for f in os.listdir(folder):
                low = f.lower()
                if low.endswith(AUDIO_EXT):
                    audios.add(low)
            if audios:
                idx[folder] = audios
        except OSError:
            continue
    return idx


def alt_names(w):
    base, _ = os.path.splitext(w.lower())
    return {base + e for e in AUDIO_EXT} | {w.lower()}


def decode_text(data):
    """Most BMS files are Shift-JIS; try strict shift_jis first, then UTF-8 (with BOM)."""
    if data[:3] == b'\xef\xbb\xbf':
        return data[3:].decode('utf-8', errors='replace'), 'utf-8-sig'
    for enc in ('shift_jis','cp932','utf-8'):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode('shift_jis', errors='replace'), 'shift_jis!'


def parse_bms(data):
    text, enc = decode_text(data)
    title = re.search(r'#TITLE\s+(.+)', text)
    artist = re.search(r'#ARTIST\s+(.+)', text)
    wavs = [w.strip() for w in re.findall(r'#WAV[\w]{2}\s+(.+)', text)]
    bmps = [b.strip() for b in re.findall(r'#BMP[\w]{2}\s+(.+)', text)]
    return {
        'title': title.group(1).strip() if title else None,
        'artist': artist.group(1).strip() if artist else None,
        'wavs': wavs,
        'bmps': bmps,
        'encoding': enc,
    }


def score_folders(wavs, idx):
    sample = wavs[:WAV_SAMPLE_MAX]
    n = len(sample)
    if n == 0:
        return [], 0
    alts = [alt_names(w) for w in sample]
    ranked = []
    for folder, audios in idx.items():
        hits = 0
        for s in alts:
            if s & audios:
                hits += 1
        if hits:
            ranked.append((hits, folder))
    ranked.sort(reverse=True, key=lambda x: x[0])
    return ranked, n


def decide(ranked, total):
    if not ranked or total == 0:
        return ('no_parent', None, None, 'no #WAV or no hits')
    top_hits, top_folder = ranked[0]
    top_ratio = top_hits / total
    second = ranked[1][0] if len(ranked) > 1 else 0
    gap = (top_hits - second) / total
    if top_ratio >= AUTO_RATIO and gap >= AUTO_GAP:
        return ('auto', top_folder, top_hits, f'ratio={top_ratio:.2f} gap={gap:.2f}')
    if top_ratio >= SKIP_RATIO:
        return ('ambiguous', top_folder, top_hits, f'ratio={top_ratio:.2f} gap={gap:.2f}')
    return ('no_parent', top_folder, top_hits, f'ratio={top_ratio:.2f} gap={gap:.2f}')


def _metadata_key(value, strip_suffix=False):
    if not value:
        return ''
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    if strip_suffix:
        value = re.sub(r'\s*\[[^\]]*\]\s*$', '', value)
        value = re.sub(r'\s*\(sp[^)]*\)\s*$', '', value, flags=re.I)
    return ''.join(ch for ch in value if ch.isalnum())


def ambiguous_candidates(ranked, meta, entry, limit=10):
    """Keep high-hit candidates while rescuing strong metadata matches.

    Generic sample banks can produce hundreds of tied 400/400 folders. A real
    parent installed late in directory order would otherwise fall outside the
    fixed top-five review window even when its artist and title match exactly.
    """
    artists = {
        _metadata_key(meta.get('artist')),
        _metadata_key(entry.get('artist')),
    } - {''}
    titles = {
        _metadata_key(meta.get('title'), strip_suffix=True),
        _metadata_key(entry.get('title'), strip_suffix=True),
    } - {''}
    metadata_matches = []
    for hits, folder in ranked:
        folder_key = _metadata_key(os.path.basename(folder))
        artist_match = any(len(key) >= 3 and key in folder_key for key in artists)
        title_match = any(len(key) >= 3 and key in folder_key for key in titles)
        score = int(artist_match) + int(title_match)
        if score:
            metadata_matches.append((score, hits, folder))
    metadata_matches.sort(key=lambda item: (item[0], item[1]), reverse=True)

    selected = []
    seen = set()
    for _, hits, folder in metadata_matches:
        if folder not in seen:
            selected.append((hits, folder))
            seen.add(folder)
    for hits, folder in ranked:
        if folder not in seen:
            selected.append((hits, folder))
            seen.add(folder)
        if len(selected) >= limit:
            break
    return selected[:limit]


def looks_like_html(blob):
    """Detect HTML/markup responses returned when url_diff points at a landing
    page rather than a direct archive. BMS charts start with '#' or ';',
    archives start with binary magic bytes — anything starting with '<' is
    markup of some kind."""
    head = blob[:512].lstrip()
    return head.startswith(b'<')


def _archive_members(blob):
    """Return [(arcname, data)] for every file in an archive blob, or None if
    the blob is not an archive at all. ZIP is read in-process; rar/7z/lzh go
    through the same 7-Zip helper install_parents.py uses, so a 差分 published
    as a .rar is no longer a dead end."""
    if blob[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                return [(n, z.read(n)) for n in z.namelist() if not n.endswith('/')]
        except zipfile.BadZipFile:
            return None
    with tempfile.TemporaryDirectory(prefix='bms_diff_arc_') as tmp:
        arc = os.path.join(tmp, 'archive.bin')
        with open(arc, 'wb') as f:
            f.write(blob)
        if install_parents.detect_archive_type(arc) is None:
            return None
        dest = os.path.join(tmp, 'x')
        ok, msg = install_parents.extract_archive(arc, dest)
        if not ok:
            raise RuntimeError(f'archive extract failed: {msg}')
        members = []
        for dirpath, _, filenames in os.walk(dest):
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, dest).replace(os.sep, '/')
                with open(path, 'rb') as f:
                    members.append((rel, f.read()))
        return members


def _arc_dirname(arcname):
    return arcname.rsplit('/', 1)[0] if '/' in arcname else ''


def extract_chart_blobs(blob, want_md5=None):
    members = _archive_members(blob)
    if members is None:
        head = blob[:200].lower()
        if b'#title' in head or b'#player' in head or b'#genre' in head:
            return [('chart.bms', blob)]
        return []
    out = [(n, d) for n, d in members
           if n.lower().endswith(CHART_EXT + BGA_EXT + AUDIO_EXT)]
    # Some url_diff downloads are bundles holding 差分 for dozens of different
    # songs (one archive per release month, say). place_files() flattens every
    # member into the chosen parent folder, so shipping the whole bundle would
    # pour unrelated songs' charts into it. Once the entry's own chart is
    # identifiable by md5, keep only what sits in the same directory as it.
    if want_md5:
        home = next((_arc_dirname(n) for n, d in out
                     if n.lower().endswith(CHART_EXT)
                     and hashlib.md5(d).hexdigest() == want_md5), None)
        if home is not None:
            beside = [(n, d) for n, d in out if _arc_dirname(n) == home]
            if beside:
                return beside
    return out


def basename_safe(arcname):
    return os.path.basename(arcname.replace('\\','/'))


def place_files(target_folder, files, dry_run):
    placed = []; skipped = []
    for arcname, data in files:
        bn = basename_safe(arcname)
        if not bn: continue
        dest = os.path.join(target_folder, bn)
        if os.path.exists(dest):
            skipped.append(bn)
            continue
        if not dry_run:
            with open(dest, 'wb') as f: f.write(data)
        placed.append(bn)
    return placed, skipped


def load_table(header_url, state_dir, refresh=True):
    """Load the table header and body, refreshing the on-disk cache by default.

    A difficulty table gains entries over time, so reusing a cached data.json
    across runs makes the pipeline silently under-report what is missing — it
    reports "Nothing to do" for charts the table has since added. We therefore
    re-fetch unless told otherwise, and fall back to the cached copy when the
    fetch fails so an offline rerun still works.
    """
    def cached(path, url, label):
        if refresh or not os.path.exists(path):
            try:
                blob, _ = fetch(url)
                with open(path, 'wb') as f: f.write(blob)
            except Exception as e:
                if not os.path.exists(path):
                    raise
                print(f'  warning: could not refresh {label} ({e}); '
                      f'falling back to the cached copy')
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    header = cached(os.path.join(state_dir, 'header.json'), header_url, 'header.json')
    data_url = urllib.parse.urljoin(header_url, header['data_url'])
    rows = cached(os.path.join(state_dir, 'data.json'), data_url, 'data.json')
    return header, rows


class State:
    def __init__(self, state_dir):
        self.results_path = os.path.join(state_dir, 'results.csv')
        self.ambig_path = os.path.join(state_dir, 'ambiguous.jsonl')
        self.no_parent_path = os.path.join(state_dir, 'no_parent.jsonl')
        self.err_path = os.path.join(state_dir, 'errors.jsonl')
        self.lock = Lock()
        self.csv_f = open(self.results_path, 'w', encoding='utf-8', newline='')
        self.csv = csv.writer(self.csv_f)
        self.csv.writerow(['md5','level','title','artist','decision',
                           'top_folder','top_hits','total_wavs','note',
                           'placed','skipped_existing'])
        self.ambig_f = open(self.ambig_path, 'w', encoding='utf-8')
        self.np_f = open(self.no_parent_path, 'w', encoding='utf-8')
        self.err_f = open(self.err_path, 'w', encoding='utf-8')
        self.counts = Counter()

    def close(self):
        self.csv_f.close(); self.ambig_f.close(); self.np_f.close(); self.err_f.close()

    def log(self, ent, decision, folder, hits, total, note, placed=None, skipped=None,
            ambig_info=None, err=None):
        with self.lock:
            self.counts[decision] += 1
            self.csv.writerow([
                ent['md5'], ent.get('level',''), ent.get('title','')[:120],
                (ent.get('artist','') or '')[:120],
                decision, os.path.basename(folder) if folder else '',
                hits or 0, total or 0, note,
                ';'.join(placed or []), ';'.join(skipped or []),
            ])
            self.csv_f.flush()
            if decision == 'ambiguous' and ambig_info is not None:
                self.ambig_f.write(json.dumps(ambig_info, ensure_ascii=False)+'\n')
                self.ambig_f.flush()
            elif decision in ('no_parent', 'bundled_in_parent'):
                self.np_f.write(json.dumps({
                    'md5': ent['md5'], 'level': ent.get('level'),
                    'title': ent.get('title'), 'artist': ent.get('artist'),
                    'top_hits': hits, 'total_wavs': total,
                    'top_folder': os.path.basename(folder) if folder else None,
                    'url_diff': ent.get('url_diff'),
                    'category': decision,
                }, ensure_ascii=False)+'\n')
                self.np_f.flush()
            elif decision in ('error', 'needs_resolution'):
                self.err_f.write(json.dumps({
                    'md5': ent['md5'], 'title': ent.get('title'),
                    'url_diff': ent.get('url_diff'), 'error': err,
                    'decision': decision,
                }, ensure_ascii=False)+'\n')
                self.err_f.flush()


def worker(ent, idx, dry_run, state, dl_cache):
    try:
        url = (ent.get('url_diff') or '').strip()
        if not url or not (url.startswith('http://') or url.startswith('https://')):
            # No separate差分 download exists: the chart ships inside the parent
            # package at ent['url']. Route it to no_parent.jsonl so that
            # install_parents.py fetches that package — it carries the chart.
            note = ('no url_diff; 差分 ships inside the parent package'
                    if not url else f'url_diff is a marker, not a URL: {url!r}')
            state.log(ent, 'bundled_in_parent', None, 0, 0, note)
            return
        cache_path = os.path.join(dl_cache, ent['md5'] + '.bin')
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 256:
            with open(cache_path,'rb') as f: blob = f.read()
        else:
            blob, _ = fetch(url)
            with open(cache_path,'wb') as f: f.write(blob)
        if looks_like_html(blob):
            state.log(ent, 'needs_resolution', None, 0, 0,
                      'HTML response (url_diff points at a landing page)',
                      err=f'HTML page returned; url_diff is not a direct archive link: {url}')
            return
        files = extract_chart_blobs(blob, ent['md5'])
        if not files:
            state.log(ent, 'error', None, 0, 0, 'no chart files in download',
                      err='extract returned empty')
            return
        chart_files = [(n,d) for n,d in files if n.lower().endswith(CHART_EXT)]
        if not chart_files:
            state.log(ent, 'error', None, 0, 0, 'no chart files',
                      err='no .bms/.bme/.bml/.pms/.bmson')
            return
        bms_for_meta = next((c for c in chart_files if not c[0].lower().endswith('.bmson')),
                            chart_files[0])
        if bms_for_meta[0].lower().endswith('.bmson'):
            try:
                obj = json.loads(bms_for_meta[1].decode('utf-8','replace'))
                wavs = [ch.get('name') for ch in obj.get('sound_channels', []) if ch.get('name')]
                meta = {'title': obj.get('info',{}).get('title'),
                        'artist': obj.get('info',{}).get('artist'),
                        'wavs': wavs, 'bmps': [], 'encoding': 'bmson'}
            except Exception as e:
                state.log(ent, 'error', None, 0, 0, 'bmson parse fail', err=str(e))
                return
        else:
            meta = parse_bms(bms_for_meta[1])

        ranked, total = score_folders(meta['wavs'], idx)
        decision, folder, top_hits, note = decide(ranked, total)

        if decision == 'auto' and folder is not None:
            placed, skipped = place_files(folder, files, dry_run)
            state.log(ent, 'auto' if not dry_run else 'auto_dry',
                      folder, top_hits, total, note, placed, skipped)
        elif decision == 'ambiguous':
            review_ranked = ambiguous_candidates(ranked, meta, ent)
            cands = [{'folder': os.path.basename(f), 'hits': h, 'path': f}
                     for h, f in review_ranked]
            state.log(ent, 'ambiguous', folder, top_hits, total, note,
                      ambig_info={
                          'md5': ent['md5'], 'title': ent.get('title'),
                          'artist': ent.get('artist'),
                          'bms_title': meta['title'], 'bms_artist': meta['artist'],
                          'total_wavs': total,
                          'candidates': cands,
                          'archive_names': [n for n,_ in files][:30],
                          'url_diff': url,
                      })
        else:
            state.log(ent, 'no_parent', folder, top_hits, total, note)
    except Exception as e:
        state.log(ent, 'error', None, 0, 0, '',
                  err=f'{type(e).__name__}: {e}\n{traceback.format_exc()}')


def _reconfigure_utf8():
    """Best-effort: reconfigure stdout to UTF-8. Safe under PyInstaller --noconsole
    (where sys.stdout may be None or a wrapped stream without reconfigure())."""
    out = sys.stdout
    if out is None or not hasattr(out, 'reconfigure'):
        return
    try:
        out.reconfigure(encoding='utf-8')
    except Exception:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--header-url', required=True,
                   help='URL of the difficulty table header.json')
    p.add_argument('--songdata-db', required=True,
                   help='Path to beatoraja songdata.db')
    p.add_argument('--music-root', required=True,
                   help='Path to the music folder root (parent of [ARTIST] TITLE/ dirs)')
    p.add_argument('--state-dir', required=True,
                   help='Where to write logs and the download cache')
    p.add_argument('--dry-run', action='store_true', default=True)
    p.add_argument('--apply', dest='dry_run', action='store_false')
    p.add_argument('--limit', type=int)
    p.add_argument('--md5')
    p.add_argument('--level')
    p.add_argument('--refresh-table', dest='refresh_table',
                   action='store_true', default=True,
                   help='Re-fetch the table header/body before running (default)')
    p.add_argument('--no-refresh-table', dest='refresh_table',
                   action='store_false',
                   help='Reuse the cached header.json/data.json in --state-dir. '
                        'Avoids two requests, but a table that gained entries '
                        'since the last run will under-report what is missing.')
    args = p.parse_args(argv)

    _reconfigure_utf8()

    state_dir = args.state_dir
    dl_cache = os.path.join(state_dir, 'downloads')
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(dl_cache, exist_ok=True)

    print(f'Mode: {"DRY-RUN" if args.dry_run else "APPLY"}')
    print(f'Table: {args.header_url}')
    print(f'State: {state_dir}')

    print('Loading songdata.db…')
    installed = installed_md5_set(args.songdata_db)
    print(f'  installed charts: {len(installed)}')

    print('Loading table…')
    header, rows = load_table(args.header_url, state_dir,
                              refresh=args.refresh_table)
    print(f'  name: {header.get("name")!r}  symbol: {header.get("symbol")!r}')
    print(f'  {len(rows)} entries')

    todo = []
    for ent in rows:
        if not ent.get('md5'): continue
        if ent['md5'] in installed: continue
        if args.md5 and ent['md5'] != args.md5: continue
        if args.level is not None and str(ent.get('level')) != str(args.level): continue
        todo.append(ent)
    if args.limit: todo = todo[:args.limit]
    n_bundled = sum(1 for e in todo
                    if not (e.get('url_diff') or '').strip()
                    .startswith(('http://', 'https://')))
    print(f'  to process: {len(todo)}'
          + (f'  ({n_bundled} bundled in parent package)' if n_bundled else ''))
    if not todo:
        print('Nothing to do.'); return 0

    if n_bundled == len(todo):
        # Bundled entries return before they ever touch the index — skip the
        # multi-minute walk of the music root.
        print('All pending entries are bundled in their parent package; '
              'skipping audio index.')
        idx = {}
    else:
        print('Building audio index…')
        t0 = time.time()
        idx = build_audio_index(args.music_root)
        print(f'  {len(idx)} folders indexed in {time.time()-t0:.1f}s')

    state = State(state_dir)
    t1 = time.time()
    completed = 0
    with ThreadPoolExecutor(max_workers=DL_WORKERS) as ex:
        futs = [ex.submit(worker, ent, idx, args.dry_run, state, dl_cache) for ent in todo]
        for fut in as_completed(futs):
            completed += 1
            if completed % 25 == 0 or completed == len(futs):
                elapsed = time.time() - t1
                rate = completed / max(elapsed, 1e-6)
                eta = (len(futs) - completed) / max(rate, 1e-6)
                auto_n = state.counts['auto'] + state.counts['auto_dry']
                print(f'  [{completed}/{len(futs)}] '
                      f'auto={auto_n} ambiguous={state.counts["ambiguous"]} '
                      f'no_parent={state.counts["no_parent"]} '
                      f'bundled={state.counts["bundled_in_parent"]} '
                      f'error={state.counts["error"]} '
                      f'elapsed={elapsed:.0f}s eta={eta:.0f}s')
    state.close()
    print('\nDone.')
    print(f'Counters: {dict(state.counts)}')
    for label, path in [('results', state.results_path), ('ambiguous (for review)', state.ambig_path),
                        ('no_parent', state.no_parent_path), ('errors', state.err_path)]:
        print(f'  {label}: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
