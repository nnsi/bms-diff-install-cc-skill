"""
Install parent BMS songs that are referenced as 親未所持 by install_diffs.py.

Reads:
  <state-dir>/no_parent.jsonl   produced by install_diffs.py
  <state-dir>/data.json         cached difficulty table

For each unique parent `url` referenced by a no_parent entry, this script:
  1. Resolves the URL through a chain of host adapters until it becomes a
     direct file download URL (or gives up).
  2. Downloads the archive into <state-dir>/parent_downloads/.
  3. Extracts it (zip via stdlib, rar/7z/lzh via 7z.exe).
  4. Reads #ARTIST and #TITLE from any chart file inside.
  5. Moves the contents to `<music-root>/[ARTIST] TITLE/`, skipping if a
     same-named folder already exists.

Output:
  <state-dir>/parent_install_log.csv

Unknown hosts and complex pages produce 'needs_haiku' rows in the log; SKILL.md
explains how to delegate those to a Haiku subagent in a follow-up pass.
"""

import argparse, csv, io, json, os, re, shutil, subprocess, sys, tempfile, time
import urllib.request, urllib.parse, zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

CHART_EXT = ('.bms','.bme','.bml','.pms','.bmson')
AUDIO_EXT = ('.wav','.ogg','.mp3','.flac','.m4a','.opus')
ARCHIVE_EXT = ('.zip','.rar','.7z','.lzh','.tar','.tgz','.tar.gz')

def _resolve_7z():
    """Locate a 7-Zip CLI. Override with BMS_DIFF_7Z env var."""
    env = os.environ.get('BMS_DIFF_7Z')
    if env and os.path.exists(env):
        return env
    for cand in ('7z', '7za', '7zz'):
        which = shutil.which(cand)
        if which:
            return which
    for cand in (
        r'C:\Program Files\7-Zip\7z.exe',
        r'C:\Program Files (x86)\7-Zip\7z.exe',
        '/usr/bin/7z', '/usr/local/bin/7z', '/opt/homebrew/bin/7z',
    ):
        if os.path.exists(cand):
            return cand
    return None  # rar/7z/lzh extraction will fail with a clear message


SEVEN_ZIP = _resolve_7z()
UA = 'Mozilla/5.0 (BMS parent installer)'
DL_TIMEOUT = 300
DL_RETRY = 2
DL_WORKERS = 2  # parents are heavy; keep concurrency low to be polite


# ---- HTTP helpers ----
def http_get(url, timeout=DL_TIMEOUT, retry=DL_RETRY, allow_redirects=True):
    last = None
    for i in range(retry+1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                final = r.geturl()
                return r.read(), r.headers.get('Content-Type',''), final
        except Exception as e:
            last = e
            time.sleep(1 + i*2)
    raise last


def http_head(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA}, method='HEAD')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.headers.get('Content-Length'), r.headers.get('Content-Type',''), r.geturl()
    except Exception:
        return None, '', url


# ---- URL adapters ----

def is_direct_archive(url):
    return bool(re.search(r'\.(zip|rar|7z|lzh|tar|tgz)(\?|#|$)', url, re.I))


def parse_manbow_event(html_bytes):
    """Extract the DownLoadAddress from a manbow event_def page."""
    # Decode (Shift-JIS most likely)
    for enc in ('utf-8','shift_jis','cp932'):
        try: text = html_bytes.decode(enc); break
        except UnicodeDecodeError: continue
    else:
        text = html_bytes.decode('utf-8', errors='replace')
    # Pattern: <Th>DownLoadAddress</B></th> <td colspan="3"><a href="URL">URL</A></td>
    m = re.search(r'DownLoadAddress.*?<a[^>]+href="([^"]+)"', text, re.S | re.I)
    if m:
        return m.group(1)
    return None


def parse_venue_bmssearch(html_bytes):
    """venue.bmssearch.net pages typically link to a GDrive file or a direct archive."""
    for enc in ('utf-8','shift_jis','cp932'):
        try: text = html_bytes.decode(enc); break
        except UnicodeDecodeError: continue
    else:
        text = html_bytes.decode('utf-8', errors='replace')
    # 1. Direct archive link
    m = re.search(r'href="(https?://[^"]+\.(?:zip|rar|7z|lzh))"', text, re.I)
    if m:
        return m.group(1)
    # 2. GDrive file link (most common on venue pages)
    m = re.search(r'href="(https?://drive\.google\.com/file/d/[^"]+)"', text)
    if m:
        return m.group(1)
    # 3. GDrive folder link
    m = re.search(r'href="(https?://drive\.google\.com/drive/folders/[^"]+)"', text)
    if m:
        return m.group(1)
    # 4. Dropbox link
    m = re.search(r'href="(https?://(?:www\.)?dropbox\.com/[^"]+)"', text)
    if m:
        return m.group(1)
    return None


def parse_gdrive_folder(folder_id):
    """Use embeddedfolderview to enumerate; return list of (file_id, filename)."""
    url = f'https://drive.google.com/embeddedfolderview?id={folder_id}#list'
    try:
        html, _, _ = http_get(url)
        text = html.decode('utf-8', errors='replace')
    except Exception:
        return []
    files = []
    for m in re.finditer(r'entry-([a-zA-Z0-9_-]{20,})[^>]*>.*?flip-entry-title">([^<]+)<', text, re.S):
        files.append((m.group(1), m.group(2)))
    return files


def pick_main_file_from_folder(files):
    """Pick the most-likely 'BMS本体' from a list of (file_id, name)."""
    if not files: return None
    if len(files) == 1: return files[0]
    # Prefer largest archive-ish file, with name not containing '差分' or 'diff' or 'sabun'
    def score(name):
        low = name.lower()
        s = 0
        if any(low.endswith(e) for e in ARCHIVE_EXT): s += 10
        if any(w in low for w in ('本体','body','full','pack','main')): s += 5
        if any(w in low for w in ('差分','sabun','diff','obj','BGA','bga','movie')): s -= 5
        return s
    return max(files, key=lambda f: score(f[1]))


def resolve(url, max_depth=4):
    """Returns ('direct', url) on success, ('needs_haiku', reason, html?) or
    ('error', reason) on failure."""
    seen = set()
    for _ in range(max_depth):
        if url in seen:
            return ('error', f'cycle at {url}')
        seen.add(url)
        try:
            p = urllib.parse.urlparse(url)
            host = (p.hostname or '').lower()
        except Exception:
            return ('error', f'bad url: {url}')

        # Dropbox first — .zip?dl=0 URLs look 'direct' but actually serve HTML
        if 'dropbox.com' in host:
            new = re.sub(r'\?dl=0\b', '?dl=1', url)
            if 'dl=1' not in new:
                new += ('&' if '?' in new else '?') + 'dl=1'
            return ('direct', new)
        if host == 'dl.dropbox.com' or host == 'dl.dropboxusercontent.com':
            return ('direct', url)

        # Direct archive URL
        if is_direct_archive(url):
            return ('direct', url)

        # GDrive file
        m = re.match(r'https?://drive\.google\.com/file/d/([^/]+)', url)
        if m:
            return ('direct', f'https://drive.google.com/uc?export=download&id={m.group(1)}')
        m = re.match(r'https?://drive\.google\.com/open\?id=([^&]+)', url)
        if m:
            return ('direct', f'https://drive.google.com/uc?export=download&id={m.group(1)}')

        # GDrive folder
        m = re.match(r'https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)', url)
        if m:
            files = parse_gdrive_folder(m.group(1))
            if not files:
                return ('needs_haiku', 'gdrive folder: could not list files')
            main = pick_main_file_from_folder(files)
            if not main:
                return ('needs_haiku', f'gdrive folder: {len(files)} files, none look main')
            url = f'https://drive.google.com/uc?export=download&id={main[0]}'
            continue

        # uc?export=download — direct
        if 'drive.google.com' in host and '/uc' in p.path:
            return ('direct', url)
        if 'drive.usercontent.google.com' in host:
            return ('direct', url)

        # Archive.org direct download paths
        if host == 'archive.org' and '/download/' in p.path:
            return ('direct', url)

        # Web Archive snapshots — usually return the file directly
        if host in ('web.archive.org','wayback.archive.org'):
            return ('direct', url)

        # Manbow event
        if host == 'manbow.nothing.sh' and 'event.cgi' in p.path:
            try:
                html, _, _ = http_get(url)
            except Exception as e:
                return ('error', f'manbow fetch failed: {e}')
            sub = parse_manbow_event(html)
            if not sub:
                return ('needs_haiku', 'manbow: DownLoadAddress not found')
            url = urllib.parse.urljoin(url, sub)
            continue

        # bmssearch venue
        if host == 'venue.bmssearch.net':
            try:
                html, _, _ = http_get(url)
            except Exception as e:
                return ('error', f'venue fetch failed: {e}')
            sub = parse_venue_bmssearch(html)
            if not sub:
                return ('needs_haiku', 'venue.bmssearch: link not found')
            url = urllib.parse.urljoin(url, sub)
            continue

        # bmssearch info page (different host)
        if host == 'bmssearch.net':
            try:
                html, _, _ = http_get(url)
            except Exception as e:
                return ('error', f'bmssearch fetch failed: {e}')
            text = html.decode('utf-8', errors='replace')
            m = re.search(r'href="(https?://[^"]+\.(?:zip|rar|7z|lzh))"', text, re.I)
            if m:
                url = m.group(1); continue
            return ('needs_haiku', 'bmssearch: no direct archive link')

        # AXFC, Mega, MediaFire — typically need JS or captcha
        if host in ('www1.axfc.net','www.axfc.net','mega.nz','www.mediafire.com',
                    'mediafire.com'):
            return ('needs_haiku', f'{host}: likely needs browser')

        # docs.google.com uc?id= form
        if host == 'docs.google.com':
            m = re.search(r'[?&]id=([^&]+)', url)
            if m:
                return ('direct', f'https://drive.google.com/uc?export=download&id={m.group(1)}')

        return ('needs_haiku', f'unknown host: {host}')

    return ('error', 'max depth exceeded')


# ---- download / extract / placement ----

def _stream_to(resp, dest):
    n = 0
    with open(dest, 'wb') as f:
        while True:
            buf = resp.read(64*1024)
            if not buf: break
            f.write(buf); n += len(buf)
    return n


def download_to(url, dest):
    """Download URL to dest. Handles Google Drive's virus-scan confirmation
    page for large files by re-submitting the embedded form."""
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    resp = urllib.request.urlopen(req, timeout=DL_TIMEOUT)
    try:
        ctype = resp.headers.get('Content-Type', '')
        # GDrive returns a tiny HTML page for files >~100MB asking to confirm
        if 'text/html' in ctype and ('drive.google.com' in url or 'drive.usercontent.google.com' in url):
            html = resp.read().decode('utf-8', errors='replace')
            am = re.search(r'<form[^>]*action="([^"]+)"', html)
            if am:
                action = am.group(1)
                params = dict(re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]+)"', html))
                qs = urllib.parse.urlencode(params)
                new_url = action + ('&' if '?' in action else '?') + qs
                resp.close()
                req2 = urllib.request.Request(new_url, headers={'User-Agent': UA})
                resp = urllib.request.urlopen(req2, timeout=DL_TIMEOUT)
        return _stream_to(resp, dest)
    finally:
        try: resp.close()
        except Exception: pass


def detect_archive_type(path):
    with open(path, 'rb') as f:
        head = f.read(8)
    if head[:2] == b'PK': return 'zip'
    if head[:7] == b'Rar!\x1a\x07\x00' or head[:8] == b'Rar!\x1a\x07\x01\x00': return 'rar'
    if head[:6] == b'7z\xbc\xaf\x27\x1c': return '7z'
    if head[2:5] == b'-lh' or head[2:5] == b'-lz': return 'lzh'
    return None


def extract_archive(archive_path, dest_dir):
    """Extract archive to dest_dir. Returns True on success."""
    kind = detect_archive_type(archive_path)
    if kind is None:
        return False, 'unknown archive type'
    os.makedirs(dest_dir, exist_ok=True)
    if kind == 'zip':
        try:
            with zipfile.ZipFile(archive_path) as z:
                z.extractall(dest_dir)
            return True, 'zip'
        except Exception as e:
            return False, f'zip extract fail: {e}'
    # rar/7z/lzh via 7-Zip
    if not SEVEN_ZIP or not os.path.exists(SEVEN_ZIP):
        return False, 'no 7-Zip on PATH; set BMS_DIFF_7Z or install 7-Zip'
    try:
        r = subprocess.run([SEVEN_ZIP, 'x', '-y', f'-o{dest_dir}', archive_path],
                          capture_output=True, timeout=600)
        if r.returncode == 0:
            return True, kind
        return False, f'7z exit {r.returncode}: {r.stderr.decode("utf-8","replace")[:200]}'
    except subprocess.TimeoutExpired:
        return False, '7z timeout'


def find_chart_meta(root):
    """Walk a directory; for the first chart file found, return (#ARTIST, #TITLE)."""
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            low = fn.lower()
            if not any(low.endswith(e) for e in CHART_EXT):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, 'rb') as f: data = f.read()
            if low.endswith('.bmson'):
                try:
                    obj = json.loads(data.decode('utf-8','replace'))
                    info = obj.get('info', {})
                    a = info.get('artist'); t = info.get('title')
                    if a and t: return a.strip(), t.strip(), dirpath
                except Exception:
                    pass
                continue
            # Plain BMS
            for enc in ('shift_jis','cp932','utf-8'):
                try: text = data.decode(enc); break
                except UnicodeDecodeError: continue
            else:
                continue
            am = re.search(r'#ARTIST\s+(.+)', text)
            tm = re.search(r'#TITLE\s+(.+)', text)
            if am and tm:
                # Strip trailing difficulty suffix from title for parent naming
                title = tm.group(1).strip()
                # Remove `[…]` or `(SP …)` style suffixes from end
                title = re.sub(r'\s*\[[^\]]*\]\s*$', '', title)
                title = re.sub(r'\s*\(SP[^)]*\)\s*$', '', title, flags=re.I)
                return am.group(1).strip(), title.strip(), dirpath
    return None, None, None


_FS_BAD = re.compile(r'[<>:"\\|?*\x00-\x1f]')
def sanitize_fs(s):
    s = _FS_BAD.sub(' ', s)
    s = s.replace('/', '／')
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip(' .')
    return s[:120]  # Windows path component sanity cap


def parent_folder_name(artist, title):
    return f'[{sanitize_fs(artist)}] {sanitize_fs(title)}'


def merge_extracted_into(src_dir, target_dir):
    """Move contents of src_dir (which may have a single nested folder) into target_dir.
    Returns count of files placed and count skipped (existing)."""
    # If src_dir has exactly one subdirectory and no files, descend into it
    while True:
        entries = os.listdir(src_dir)
        if len(entries) == 1 and os.path.isdir(os.path.join(src_dir, entries[0])):
            src_dir = os.path.join(src_dir, entries[0])
        else:
            break
    os.makedirs(target_dir, exist_ok=True)
    placed = 0; skipped = 0
    for dirpath, _, filenames in os.walk(src_dir):
        rel = os.path.relpath(dirpath, src_dir)
        target_sub = target_dir if rel == '.' else os.path.join(target_dir, rel)
        os.makedirs(target_sub, exist_ok=True)
        for fn in filenames:
            src = os.path.join(dirpath, fn)
            dst = os.path.join(target_sub, fn)
            if os.path.exists(dst):
                skipped += 1; continue
            shutil.move(src, dst); placed += 1
    return placed, skipped


# ---- pipeline ----

def process_parent(parent_url, hint_artist, hint_title, music_root, dl_cache,
                   md5_examples, dry_run):
    """Returns dict with status info."""
    out = {
        'parent_url': parent_url,
        'hint_artist': hint_artist,
        'hint_title': hint_title,
        'md5_examples': ';'.join(md5_examples[:3]),
        'resolved_url': '',
        'status': '',
        'reason': '',
        'folder_created': '',
        'size_bytes': 0,
    }
    # Resolve URL chain
    res = resolve(parent_url)
    if res[0] != 'direct':
        out['status'] = res[0]
        out['reason'] = res[1]
        return out
    direct_url = res[1]
    out['resolved_url'] = direct_url

    if dry_run:
        out['status'] = 'dry-resolved'
        return out

    # Cache key from URL hash
    key = re.sub(r'[^A-Za-z0-9._-]','_', direct_url)[:120]
    archive_path = os.path.join(dl_cache, f'{key}.bin')
    if not (os.path.exists(archive_path) and os.path.getsize(archive_path) > 1024):
        try:
            size = download_to(direct_url, archive_path)
            out['size_bytes'] = size
        except Exception as e:
            out['status'] = 'error'
            out['reason'] = f'download fail: {e}'
            return out
    else:
        out['size_bytes'] = os.path.getsize(archive_path)

    # Extract to temp
    with tempfile.TemporaryDirectory(prefix='bms_parent_') as tmp:
        ok, msg = extract_archive(archive_path, tmp)
        if not ok:
            out['status'] = 'error'
            out['reason'] = f'extract: {msg}'
            return out
        artist, title, _ = find_chart_meta(tmp)
        if not artist or not title:
            # Fall back to hint values from table
            if hint_artist and hint_title:
                artist = hint_artist
                title = re.sub(r'\s*\[[^\]]*\]\s*$', '', hint_title).strip() or hint_title
            else:
                out['status'] = 'error'
                out['reason'] = 'could not determine artist/title'
                return out
        folder = parent_folder_name(artist, title)
        target = os.path.join(music_root, folder)
        if os.path.exists(target):
            out['status'] = 'exists'
            out['folder_created'] = folder
            out['reason'] = 'target folder already exists; skipped'
            return out
        placed, skipped = merge_extracted_into(tmp, target)
        out['status'] = 'installed'
        out['folder_created'] = folder
        out['reason'] = f'{placed} files placed, {skipped} skipped'
    return out


def _reconfigure_utf8():
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
    p.add_argument('--state-dir', required=True)
    p.add_argument('--music-root', required=True)
    p.add_argument('--dry-run', action='store_true', default=False,
                   help='Resolve URLs but do not download or extract')
    p.add_argument('--limit', type=int, help='Process at most N unique parents')
    p.add_argument('--host', help='Only process URLs from this hostname')
    p.add_argument('--md5', help='Only process the parent of this差分 md5')
    p.add_argument('--overrides', help='JSON file mapping original parent URL to a replacement direct URL (output of a Haiku resolution pass)')
    args = p.parse_args(argv)

    _reconfigure_utf8()

    state_dir = args.state_dir
    dl_cache = os.path.join(state_dir, 'parent_downloads')
    os.makedirs(dl_cache, exist_ok=True)

    no_parent_path = os.path.join(state_dir, 'no_parent.jsonl')
    data_path = os.path.join(state_dir, 'data.json')
    if not os.path.exists(no_parent_path):
        print(f'no_parent.jsonl missing at {no_parent_path}; run install_diffs.py first.', file=sys.stderr)
        return 1
    if not os.path.exists(data_path):
        print(f'data.json missing at {data_path}; run install_diffs.py first.', file=sys.stderr)
        return 1

    with open(data_path, 'r', encoding='utf-8') as f:
        table_by_md5 = {e['md5']: e for e in json.load(f)}
    with open(no_parent_path, 'r', encoding='utf-8') as f:
        no_parent = [json.loads(l) for l in f]

    # Group by parent URL
    by_url = defaultdict(lambda: {'md5s': [], 'title': None, 'artist': None})
    for np in no_parent:
        md5 = np['md5']
        ent = table_by_md5.get(md5)
        if not ent or not ent.get('url'):
            continue
        u = ent['url']
        by_url[u]['md5s'].append(md5)
        if not by_url[u]['title']:
            by_url[u]['title'] = ent.get('title')
            by_url[u]['artist'] = ent.get('artist')

    # Optional override map: {original_parent_url: replacement_url}
    overrides = {}
    if args.overrides and os.path.exists(args.overrides):
        with open(args.overrides, 'r', encoding='utf-8') as f:
            overrides = json.load(f)
        print(f'  loaded {len(overrides)} URL overrides from {args.overrides}')

    targets = list(by_url.items())
    if args.host:
        targets = [t for t in targets
                   if (urllib.parse.urlparse(t[0]).hostname or '').lower() == args.host.lower()]
    if args.md5:
        targets = [t for t in targets if args.md5 in t[1]['md5s']]
    if args.limit:
        targets = targets[:args.limit]

    print(f'unique parent URLs to attempt: {len(targets)}')
    if not targets:
        print('nothing to do'); return 0

    log_path = os.path.join(state_dir, 'parent_install_log.csv')
    lock = Lock()
    counts = defaultdict(int)
    with open(log_path, 'w', encoding='utf-8', newline='') as fp:
        writer = csv.writer(fp)
        writer.writerow(['parent_url','resolved_url','status','reason',
                         'hint_artist','hint_title','folder_created',
                         'size_bytes','md5_examples'])
        def run(item):
            url, info = item
            # If Haiku (or manual mapping) provided a replacement, treat that as the start URL
            url_for_resolve = overrides.get(url, url)
            r = process_parent(url_for_resolve, info['artist'], info['title'],
                               args.music_root, dl_cache, info['md5s'], args.dry_run)
            r['parent_url'] = url  # preserve the original URL in the log for traceability
            if url_for_resolve != url:
                r['reason'] = (r.get('reason','') + f' [override from {url}]').strip()
            with lock:
                counts[r['status']] += 1
                writer.writerow([r['parent_url'], r['resolved_url'], r['status'],
                                r['reason'], r['hint_artist'] or '', r['hint_title'] or '',
                                r['folder_created'], r['size_bytes'], r['md5_examples']])
                fp.flush()
                # Progress
                done = sum(counts.values())
                if done % 5 == 0 or done == len(targets):
                    print(f'  [{done}/{len(targets)}] {dict(counts)}')
            return r

        if args.dry_run:
            # No parallel; just sequential for resolve()
            for item in targets:
                run(item)
        else:
            with ThreadPoolExecutor(max_workers=DL_WORKERS) as ex:
                futs = [ex.submit(run, item) for item in targets]
                for _ in as_completed(futs):
                    pass

    print(f'\nDone. counts={dict(counts)}')
    print(f'log: {log_path}')
    print(f'archives cached in: {dl_cache}')
    print('\nNext: rerun install_diffs.py --apply to place差分 against the newly installed parents.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
