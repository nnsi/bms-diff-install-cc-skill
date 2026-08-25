"""
Produce a consolidated report of BMS差分 that were NOT installed, with the
reason for each and the source URL(s) the user would need to grab manually.

Cross-joins:
  - results.csv           overall decision per差分 (auto/ambiguous/no_parent/error)
  - no_parent.jsonl       差分 whose parent isn't installed (after diff pipeline)
  - errors.jsonl          差分 with download / parse failures
  - review_decisions.json Codex place/skip judgments (skip = couldn't place)
  - review_apply_log.csv  reviewed placements that failed during application
  - parent_install_log.csv parent URL resolution status (installed / needs_browser / error)
  - data.json             cached difficulty table (for joining titles/URLs)

Outputs:
  <state-dir>/unrecovered.csv
  <state-dir>/unrecovered.md
"""

import argparse, csv, json, os, sys
from collections import Counter, defaultdict


def load_jsonl(path):
    if not os.path.exists(path): return []
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def load_csv(path):
    if not os.path.exists(path): return []
    with open(path, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


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
    p.add_argument('--format', choices=['md','csv','both'], default='both')
    args = p.parse_args(argv)
    _reconfigure_utf8()

    s = args.state_dir
    table_path = os.path.join(s, 'data.json')
    if not os.path.exists(table_path):
        print(f'data.json missing at {table_path}; run install_diffs.py first.', file=sys.stderr)
        return 1
    with open(table_path, 'r', encoding='utf-8') as f:
        table = {e['md5']: e for e in json.load(f)}

    no_parent = load_jsonl(os.path.join(s, 'no_parent.jsonl'))
    errors    = load_jsonl(os.path.join(s, 'errors.jsonl'))
    parent_log = {row['parent_url']: row for row in load_csv(os.path.join(s, 'parent_install_log.csv'))}

    review_skipped = {}
    decisions_path = os.path.join(s, 'review_decisions.json')
    legacy_path = os.path.join(s, 'haiku_decisions.json')
    if not os.path.exists(decisions_path) and os.path.exists(legacy_path):
        decisions_path = legacy_path
    if os.path.exists(decisions_path):
        with open(decisions_path, 'r', encoding='utf-8') as f:
            for d in json.load(f):
                if d.get('decision') == 'skip':
                    review_skipped[d['md5']] = d.get('reason', '')

    review_log_path = os.path.join(s, 'review_apply_log.csv')
    legacy_review_log = os.path.join(s, 'haiku_apply_log.csv')
    if not os.path.exists(review_log_path) and os.path.exists(legacy_review_log):
        review_log_path = legacy_review_log
    review_errors = {
        row['md5']: row for row in load_csv(review_log_path)
        if row.get('status') == 'error'
    }

    np_md5s = {np['md5'] for np in no_parent}
    rows = []
    for np in no_parent:
        md5 = np['md5']
        ent = table.get(md5, {})
        parent_url = ent.get('url', '')
        ps = parent_log.get(parent_url, {})
        reason = ps.get('status') or 'never_attempted'
        if ps.get('reason'):
            reason += f' — {ps["reason"][:60]}'
        rows.append({
            'md5': md5,
            'level': np.get('level') or ent.get('level','') or '',
            'title': np.get('title') or ent.get('title',''),
            'artist': np.get('artist') or ent.get('artist','') or '',
            'category': np.get('category') or 'no_parent',
            'reason': f'parent: {reason}',
            'parent_url': parent_url,
            'url_diff': np.get('url_diff') or ent.get('url_diff','') or '',
        })

    for md5, review_reason in review_skipped.items():
        if md5 in np_md5s: continue  # already covered
        ent = table.get(md5, {})
        rows.append({
            'md5': md5,
            'level': ent.get('level','') or '',
            'title': ent.get('title',''),
            'artist': ent.get('artist','') or '',
            'category': 'review_skip',
            'reason': f'Review: {review_reason[:80]}',
            'parent_url': ent.get('url',''),
            'url_diff': ent.get('url_diff',''),
        })

    for md5, apply_row in review_errors.items():
        if md5 in np_md5s or md5 in review_skipped:
            continue
        ent = table.get(md5, {})
        rows.append({
            'md5': md5,
            'level': ent.get('level','') or '',
            'title': ent.get('title',''),
            'artist': ent.get('artist','') or '',
            'category': 'review_error',
            'reason': (apply_row.get('reason') or 'reviewed placement failed')[:120],
            'parent_url': ent.get('url',''),
            'url_diff': ent.get('url_diff',''),
        })

    for e in errors:
        md5 = e['md5']
        if md5 in np_md5s or md5 in review_errors:
            continue
        ent = table.get(md5, {})
        rows.append({
            'md5': md5,
            'level': ent.get('level','') or '',
            'title': e.get('title','') or ent.get('title',''),
            'artist': ent.get('artist','') or '',
            'category': e.get('decision') or 'dl_error',
            'reason': (e.get('error','') or '')[:120],
            'parent_url': ent.get('url',''),
            'url_diff': e.get('url_diff') or ent.get('url_diff',''),
        })

    rows.sort(key=lambda r: (str(r['level']).zfill(3), r['md5']))

    cats = Counter(r['category'] for r in rows)
    print(f'unrecovered差分: {len(rows)}  (categories: {dict(cats)})')

    if args.format in ('csv','both'):
        out = os.path.join(s, 'unrecovered.csv')
        with open(out, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['md5','level','title','artist',
                                              'category','reason','parent_url','url_diff'])
            w.writeheader()
            for r in rows: w.writerow(r)
        print(f'wrote {out}')

    if args.format in ('md','both'):
        out = os.path.join(s, 'unrecovered.md')
        # Group by parent URL so the user can knock them out per-parent
        by_parent = defaultdict(list)
        no_parent_url = []
        for r in rows:
            if r['parent_url']:
                by_parent[r['parent_url']].append(r)
            else:
                no_parent_url.append(r)

        def esc(s):
            return str(s).replace('|','\\|').replace('\n',' ').strip()

        with open(out, 'w', encoding='utf-8') as f:
            f.write(f'# Unrecovered差分 ({len(rows)})\n\n')
            f.write('## Summary\n\n')
            for c, n in cats.most_common():
                f.write(f'- **{c}**: {n}\n')
            f.write('\n')

            if by_parent:
                f.write('## Grouped by parent URL\n\n')
                f.write('Rows sharing a parent URL likely all unblock at once if you install '
                        'that parent manually.\n\n')
                ordered = sorted(by_parent.items(), key=lambda kv: -len(kv[1]))
                for url, group in ordered:
                    ps = parent_log.get(url, {})
                    status = ps.get('status', 'never_attempted')
                    reason = ps.get('reason','')
                    f.write(f'### {len(group)} 差分 — parent status: `{status}`\n\n')
                    f.write(f'- Parent URL: <{url}>\n')
                    if ps.get('resolved_url'):
                        f.write(f'- Resolved (best attempt): <{ps["resolved_url"]}>\n')
                    if reason:
                        f.write(f'- Reason: {esc(reason)}\n')
                    f.write('\n')
                    f.write('| md5 | Lv | Title | Diff URL |\n')
                    f.write('|---|---|---|---|\n')
                    for r in group:
                        f.write(f'| `{r["md5"][:8]}` | {esc(r["level"])} | '
                                f'{esc(r["title"])} | <{r["url_diff"]}> |\n')
                    f.write('\n')

            if no_parent_url:
                f.write(f'## Without parent URL ({len(no_parent_url)})\n\n')
                f.write('| md5 | Lv | Title | Artist | Category | Reason | Diff URL |\n')
                f.write('|---|---|---|---|---|---|---|\n')
                for r in no_parent_url:
                    f.write(f'| `{r["md5"][:8]}` | {esc(r["level"])} | '
                            f'{esc(r["title"])} | {esc(r["artist"])} | '
                            f'{r["category"]} | {esc(r["reason"])} | '
                            f'<{r["url_diff"]}> |\n')
                f.write('\n')

        print(f'wrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
