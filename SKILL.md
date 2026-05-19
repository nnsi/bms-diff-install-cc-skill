---
name: bms-diff-install
description: Install BMS差分 (chart differentials) and optionally their parent songs from a beatoraja-compatible difficulty table. Downloads each entry's url_diff, finds the matching parent song folder under the user's music root by matching #WAV keysound filenames, and copies chart files into place without overwriting. Clear matches go through deterministic scoring; ambiguous cases are batch-judged by a Haiku subagent; charts whose parent isn't installed are skipped — unless `--install-parents` is on, in which case the parent's source URL is resolved through host adapters (manbow / venue.bmssearch / GDrive / Dropbox / archive.org / ...) and downloaded too. Use when the user asks to install / sync / pull-down 差分 from a 難易度表 (difficulty table) URL.
---

# Install BMS差分 from a difficulty table

You are guiding the user through installing BMS差分 (chart-only files that reference an existing parent song's keysounds) from a beatoraja-compatible difficulty table (`<meta name="bmstable" content="header.json">` style).

## Phases

1. **Gather parameters** — confirm table URL and local paths
2. **Dry-run** — score every entry, partition into auto / ambiguous / no_parent / error
3. **(Optional) Install parents** — for `no_parent` charts, download the parent songs from their `url` field. Triggered when the user says "install parents too" / "親もDL" / "include parents" / "`--install-parents`". After installing parents, loop back to phase 2 (the previously-skipped差分 now have matchable parents).
4. **Apply auto** — place files for clear matches
5. **Haiku batch** — delegate the ambiguous cases to a Haiku subagent, then apply its decisions
6. **GDrive folder rescue** — for any remaining errors caused by GDrive folder URLs on `url_diff` (the script auto-handles GDrive `/file/d/` URLs but not folder URLs for diffs), enumerate via `embeddedfolderview` and rerun for those md5s
7. **Report** — summarize placements, tell the user to rescan in beatoraja

The scripts live alongside this SKILL.md:
- `scripts/install_diffs.py` — main差分 pipeline (scoring, placement, dry-run/apply)
- `scripts/install_parents.py` — parent-song downloader (phase 3)
- `scripts/prepare_haiku_input.py` — slims `ambiguous.jsonl` for Haiku
- `scripts/apply_haiku.py` — applies Haiku's decisions

Refer to them with `${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/bms-diff-install/scripts/<name>.py` or by absolute path on Windows. On this user's machine they're at `C:\Users\norfa\.claude\skills\bms-diff-install\scripts\`.

## Phase 1: gather parameters

You need four things. Probe the environment first, then ask the user only for what you can't infer.

- **`HEADER_URL`** — URL of the table's `header.json`. If the user gave a page URL (e.g. `https://potechang.github.io/like_st/`), fetch it and read the `<meta name="bmstable" content="...">` value — that relative URL resolves to the header JSON. If you can't infer, ask.
- **`SONGDATA_DB`** — beatoraja's `songdata.db`. Typically `<beatoraja-install>/songdata.db`. Look in the cwd first; otherwise ask.
- **`MUSIC_ROOT`** — root of the music library (parent of `[ARTIST] TITLE/` folders). Usually `../music` relative to the beatoraja install. Confirm by `ls`.
- **`STATE_DIR`** — where to write logs and the download cache. Default: `<beatoraja-install>/_install_diff/state`. Keep it inside the beatoraja install so it survives across invocations and the DL cache is reused.

Verify all four exist before continuing.

## Phase 2: dry-run

```
python <scripts>/install_diffs.py \
  --header-url "$HEADER_URL" \
  --songdata-db "$SONGDATA_DB" \
  --music-root  "$MUSIC_ROOT" \
  --state-dir   "$STATE_DIR" \
  --dry-run
```

Expect roughly 5–10 minutes for a table of ~1500 entries on first run (network-bound). Subsequent runs reuse `<state-dir>/downloads/` and finish in seconds.

When it finishes, report the counters:
- `auto_dry` — clear matches, will be placed in phase 3
- `ambiguous` — go to Haiku in phase 4
- `no_parent` — parent song not installed; skipped (user can install the parent and rerun later)
- `error` — usually GDrive folder URLs or dead links; handle in phase 5

Show the user the counts and confirm before applying. Do NOT auto-apply without explicit confirmation.

## Phase 3 (optional): install parents

Only run this if the user asked for parent download. Otherwise skip to phase 4.

If `<state-dir>/no_parent.jsonl` is empty, also skip.

Before running, estimate the size and **confirm with the user**: typical parent
song zips are ~50–150MB each, so N parents can mean tens of GB. The script
groups entries by parent URL (dedup), so the actual download count is usually
smaller than the no_parent line count. Show the unique-URL count first if you
want to be precise — that's `wc -l no_parent.jsonl` is an over-count; the
script reports the deduped count when it starts.

```
python <scripts>/install_parents.py \
  --state-dir   "$STATE_DIR" \
  --music-root  "$MUSIC_ROOT" \
  [--dry-run]    # resolve URLs only, no download (recommended first)
  [--limit N]    # cap unique parents
  [--host HOST]  # only this hostname (e.g. manbow.nothing.sh)
  [--md5 HASH]   # only the parent of one 差分
```

The script resolves each URL through host adapters:
- **Direct archive** (`.zip`/`.rar`/`.7z`/`.lzh`) → download
- **manbow.nothing.sh/event/event.cgi** → parse `<Th>DownLoadAddress</B></th><td><a href=URL>` and recurse on that URL
- **venue.bmssearch.net** → grab the page's archive/GDrive/Dropbox link, recurse
- **drive.google.com/file/d/{ID}/** → normalize to `uc?export=download&id={ID}`
- **drive.google.com/drive/folders/{ID}** → enumerate via `embeddedfolderview`, pick the most-likely BMS本体 (largest archive, no "差分"/"sabun"/"diff" in name), recurse
- **dropbox.com/...?dl=0** → swap to `?dl=1`
- **dl.dropbox.com / dl.dropboxusercontent.com** → use as-is
- **archive.org/download/...** → use as-is
- **web.archive.org / wayback.archive.org** → use as-is
- **docs.google.com/uc?id=...** → normalize
- **bmssearch.net info page** → find embedded archive link
- **Unknown / AXFC / Mega / MediaFire / Wix / OneDrive / etc.** → status `needs_haiku`

For `needs_haiku` rows, spawn a Haiku subagent (`model: "haiku"`) with this
template:

```
Read <state-dir>/parent_install_log.csv and pick the rows where status ==
"needs_haiku". For each one, fetch the parent_url via WebFetch or the
browser MCP, locate the BMS本体 download link (look for "本体"/"Body"/"Full"
labels, the largest .zip/.rar/.7z, or the only file when there's no
ambiguity). Avoid links labelled "差分" / "sabun" / "diff" / "BGA only".

Write decisions to <state-dir>/parent_haiku_urls.json — an array of
{parent_url, resolved_url}. The user will then rerun:
  install_parents.py --state-dir ... --music-root ... --override <file>
(NOTE: the --override flag is not yet implemented; for now, manually update
the script's resolve() or just feed the new URLs back as raw direct URLs by
editing the URL → adapter mapping.)
```

(Future work: a `--override <jsonfile>` flag on `install_parents.py` to feed
Haiku's resolutions back in cleanly. For now the unresolved cases are
listed in the CSV for inspection.)

Post-install, **re-run phase 2 (dry-run)** to refresh `no_parent.jsonl` /
`ambiguous.jsonl` / `results.csv` — many of the previously-no_parent差分
should now auto-match.

Tools required on the machine:
- 7-Zip at `C:\Program Files\7-Zip\7z.exe` (or equivalent for non-Windows)
  for `.rar` / `.7z` / `.lzh` extraction. `.zip` works via stdlib.

The download cache lives at `<state-dir>/parent_downloads/` and is keyed by
the resolved direct URL; reruns are free.

## Phase 4: apply auto

```
python <scripts>/install_diffs.py ... --apply
```

Same args as phase 2 but `--apply` replaces `--dry-run`. This re-runs the whole pipeline using the cached downloads, so it's fast. It only writes files for `auto` decisions; ambiguous/no_parent/error are still just logged.

The script never overwrites existing files; if a差分 with the same basename is already in the target folder, it logs to `skipped_existing` and moves on.

## Phase 5: Haiku batch judgment

If `state.counts['ambiguous'] == 0`, skip this phase.

Otherwise:

a. Slim the ambiguous file:
```
python <scripts>/prepare_haiku_input.py --state-dir "$STATE_DIR"
```
This writes `<state-dir>/haiku_input.json` (a JSON array of cases).

b. Spawn a Haiku subagent via the Agent tool with `model: "haiku"`. Use the prompt template below — it's structured to handle the common pathological cases:

```
You are judging where to place BMS差分 (chart differentials). For each input
case, decide which already-installed parent music folder is the correct one
— or that none of the candidates match.

Read: <state-dir>/haiku_input.json

Each case has:
- md5: identifier (pass-through)
- table_title, table_artist: from the difficulty table
- bms_title, bms_artist: parsed from the BMS file itself (more authoritative)
- total_wavs: chart's keysound reference count
- candidates: up to 5 candidate folders with hit counts on the chart's keysounds

DECISION RULES:
1. Strong artist+title match wins. Folder names follow `[ARTIST] TITLE`.
   The chart's title often has a suffix like [Eternity] or [remix]; strip
   that to recover the parent's base title.
2. When multiple candidates tie at full hits (often 400/400 across 5
   folders), it usually means the chart uses common sample-bank names.
   Judge by artist/title against folder names; hit count is meaningless.
3. Watch for reversed/obfuscated artist names: "niart kcalb / IKIHS" reads
   as "black train / SHIKI" reversed — that's a SHIKI chart.
4. If no candidate's folder name matches the artist, mark "skip".
5. Character variations are fine: fullwidth `：` vs `:`, `／` vs `/`,
   spacing, brackets — all tolerable.
6. Dramatic hit-count gap (top >> 2nd) is reliable when artist also
   matches; still skip if artist truly doesn't appear.
7. If two folders both legitimately match the artist (e.g. `[SHIKI] Air`
   and `[SHIKI／IIDX17] Air -★10-`), prefer the simpler/original one.

OUTPUT: Write JSON to <state-dir>/haiku_decisions.json:
[
  {"md5": "...", "decision": "place"|"skip", "folder": "<exact basename or null>", "reason": "..."}
]

Folder names MUST be copy/pasted verbatim from `candidates[].folder`.
Output exactly one entry per input case.

Use Write tool only — don't print the JSON in your response, just confirm
you wrote it and briefly note any tricky calls.
```

c. Apply Haiku's decisions:
```
python <scripts>/apply_haiku.py --state-dir "$STATE_DIR" --music-root "$MUSIC_ROOT"
```

Report the resulting `placed` / `skipped` / `errors` counts.

## Phase 6: GDrive folder rescue (optional)

Check `<state-dir>/errors.jsonl`. For entries whose `url_diff` is a
`https://drive.google.com/drive/folders/{FOLDER_ID}` URL:

a. Fetch `https://drive.google.com/embeddedfolderview?id={FOLDER_ID}#list`
   (returns simple HTML).
b. Extract file IDs from `entry-{FILE_ID}` divs.
c. For each file, download via
   `https://drive.google.com/uc?export=download&id={FILE_ID}` and save to
   `<state-dir>/downloads/{md5}.bin`.
   - If the folder has multiple files (chart + BGA), zip them first so
     `extract_chart_blobs` can read them. Or just save the chart file
     directly if there's only one.
d. Rerun `install_diffs.py --apply --md5 <hash>` for each rescued entry —
   the populated cache will route it through scoring and placement.

For non-GDrive errors (dead links, unexpected formats), report them and move on.

## Phase 7: report

Tell the user:

- Total placed across phases 3, 4, 5
- How many `no_parent` charts are still waiting (suggest installing the
  parent songs and rerunning the skill to pick them up — the DL cache means
  it'll be fast)
- **They must rescan in beatoraja** — placing files doesn't update
  `songdata.db`. F5 or the "楽曲データベース更新" menu, or set
  `"updatesong": true` in `config.json` for auto-scan on startup.
- If they registered the table URL in beatoraja's difficulty-table list,
  the newly placed差分 will all show up there after the rescan.

## Notes and gotchas

- **Encoding**: BMS files are almost always Shift-JIS. `decode_text` tries
  `shift_jis` first then UTF-8. If you see surrogates in `bms_artist`, the
  file isn't actually broken — your terminal probably can't render Japanese.
  Confirm by checking the raw UTF-8 bytes of the state files (`xxd`).
- **songdata.db `text_factory=bytes`**: beatoraja stores some columns as
  raw bytes that may be mojibake'd, so `installed_md5_set` uses bytes mode
  and only decodes `md5` as ASCII. Don't try to read titles/paths from the
  DB — work from the filesystem instead.
- **Don't widen tolerances**: `AUTO_RATIO=0.95`, `AUTO_GAP=0.20` are tuned
  to avoid false positives. Tied 400/400 candidates are common when差分
  use only common sample names (kick/snare/etc.); those should fall to
  Haiku, not auto.
- **Resume is free**: rerunning the skill on the same table reuses the
  download cache. Only entries whose md5 isn't yet in songdata.db are
  processed, so once the user rescans, a rerun is essentially a no-op.
- **Cost discipline**: don't have Haiku judge the auto cases too — they're
  unambiguous (100% hits with a huge gap). Only feed it `ambiguous.jsonl`.
- **Parent folder naming**: `install_parents.py` derives the folder name from
  the *parent* song's #TITLE / #ARTIST inside the downloaded archive, with the
  差分 suffix (`[Eternity]`, `(SP …)` at the end of the title) stripped. The
  result is `[ARTIST] TITLE` matching beatoraja's convention. Conflicts with
  existing folders are reported as `exists` and not overwritten.
- **GDrive virus-scan**: `install_parents.py` automatically detects the
  ~100MB+ confirmation page and re-POSTs the embedded form. No user
  intervention needed.
