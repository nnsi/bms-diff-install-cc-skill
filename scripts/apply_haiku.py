"""Apply Haiku's parent-folder decisions to the ambiguous差分 cases.

Reads <state-dir>/haiku_decisions.json (produced by a Haiku subagent), looks up
each md5's cached download, and either places the chart files into the chosen
folder (for "place" decisions) or logs the skip reason.

Output: <state-dir>/haiku_apply_log.csv
"""
import argparse, json, os, sys, io, zipfile, csv

CHART_EXT = ('.bms','.bme','.bml','.pms','.bmson')
BGA_EXT   = ('.bmp','.png','.jpg','.jpeg','.gif','.wmv','.mp4','.avi','.mov','.webm','.mpg','.mpeg')
AUDIO_EXT = ('.wav','.ogg','.mp3','.flac','.m4a','.opus')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--state-dir', required=True)
    p.add_argument('--music-root', required=True)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')

    decisions_path = os.path.join(args.state_dir, 'haiku_decisions.json')
    dl_cache = os.path.join(args.state_dir, 'downloads')
    with open(decisions_path, 'r', encoding='utf-8') as f:
        decisions = json.load(f)
    print(f'decisions: {len(decisions)}')

    all_folders = {}
    for n in os.listdir(args.music_root):
        p = os.path.join(args.music_root, n)
        if os.path.isdir(p):
            all_folders[n] = p

    log_rows = []
    placed_n = skipped_n = err_n = 0
    for d in decisions:
        md5 = d['md5']
        cache = os.path.join(dl_cache, md5 + '.bin')
        if d['decision'] == 'skip':
            skipped_n += 1
            log_rows.append([md5, 'skip', d.get('folder',''), d.get('reason',''), '', ''])
            continue
        folder_name = d.get('folder')
        if not folder_name or folder_name not in all_folders:
            err_n += 1
            log_rows.append([md5, 'error', folder_name or '', 'folder not found on disk', '', ''])
            continue
        target = all_folders[folder_name]
        if not os.path.exists(cache):
            err_n += 1
            log_rows.append([md5, 'error', folder_name, 'cache file missing', '', ''])
            continue
        with open(cache,'rb') as f: blob = f.read()
        placed = []; skipped_existing = []
        try:
            if blob[:2] == b'PK':
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    for n in z.namelist():
                        if n.endswith('/'): continue
                        low = n.lower()
                        if not (low.endswith(CHART_EXT) or low.endswith(BGA_EXT) or low.endswith(AUDIO_EXT)):
                            continue
                        bn = os.path.basename(n.replace('\\','/'))
                        if not bn: continue
                        dest = os.path.join(target, bn)
                        if os.path.exists(dest):
                            skipped_existing.append(bn); continue
                        with open(dest, 'wb') as g: g.write(z.read(n))
                        placed.append(bn)
            else:
                head = blob[:200].lower()
                if b'#title' in head or b'#player' in head or b'#genre' in head:
                    bn = f'{md5[:12]}.bms'
                    dest = os.path.join(target, bn)
                    if os.path.exists(dest):
                        skipped_existing.append(bn)
                    else:
                        with open(dest,'wb') as g: g.write(blob)
                        placed.append(bn)
                else:
                    err_n += 1
                    log_rows.append([md5, 'error', folder_name, 'not zip and not bms', '', ''])
                    continue
            placed_n += 1
            log_rows.append([md5, 'placed', folder_name, d.get('reason',''),
                             ';'.join(placed), ';'.join(skipped_existing)])
        except Exception as e:
            err_n += 1
            log_rows.append([md5, 'error', folder_name, str(e), '', ''])

    out_csv = os.path.join(args.state_dir, 'haiku_apply_log.csv')
    with open(out_csv, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['md5','status','folder','reason','placed','skipped_existing'])
        w.writerows(log_rows)

    print(f'placed: {placed_n}')
    print(f'skipped: {skipped_n}')
    print(f'errors: {err_n}')
    print(f'log: {out_csv}')


if __name__ == '__main__':
    main()
