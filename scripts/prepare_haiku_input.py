"""Slim ambiguous.jsonl down to the fields Haiku needs."""
import argparse, json, os, sys

p = argparse.ArgumentParser()
p.add_argument('--state-dir', required=True)
args = p.parse_args()
sys.stdout.reconfigure(encoding='utf-8')

src = os.path.join(args.state_dir, 'ambiguous.jsonl')
dst = os.path.join(args.state_dir, 'haiku_input.json')

with open(src, 'r', encoding='utf-8') as f:
    cases = [json.loads(l) for l in f]
slim = []
for c in cases:
    slim.append({
        'md5': c['md5'],
        'table_title': c.get('title'),
        'table_artist': c.get('artist'),
        'bms_title': c.get('bms_title'),
        'bms_artist': c.get('bms_artist'),
        'total_wavs': c.get('total_wavs'),
        'candidates': [{'folder': cc['folder'], 'hits': cc['hits']}
                       for cc in c.get('candidates', [])[:5]],
    })
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(slim, f, ensure_ascii=False, indent=1)
print(f'wrote {len(slim)} cases to {dst}')
