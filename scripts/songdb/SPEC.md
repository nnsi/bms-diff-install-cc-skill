# songdb — Behavior Specification

This document describes WHAT the `scripts/songdb/` package must do.
It is intentionally written without reference to any specific BMS-player
source code; everything below either references a public specification
(BMS / BMSON / hash RFCs / SQLite) or describes behavior that must be
**derived empirically** by the implementer (e.g. by running SQL queries
against a real beatoraja `songdata.db`).

## Purpose

When the parent `bms-diff-install` skill drops a chart file into a music
folder, beatoraja can only see it after an `F5` rescan that walks the
entire library. For users with tens of thousands of songs this is slow.
The `songdb` package inserts a fully-populated `song` row directly into
`songdata.db` so the chart appears in beatoraja's selector on next launch
with no rescan.

## Scope

- **Input**: BMS files (`.bms`, `.bme`, `.bml`, `.pms`), BMSON files
  (`.bmson`), a target `songdata.db` SQLite file, and the music root
  directory.
- **Output**: rows appended to the `song` table in the existing DB.

## Public references

- **BMS format**: standard `#`-command + channel chart format. Multiple
  community references — e.g. hitkey's BMS commentary, the LR2 spec
  PDF, BMSE documentation. Search "BMS format spec" or "BMS file
  channel list".
- **BMSON format**: JSON-based modern alternative. Spec lives at
  <https://bmson-spec.readthedocs.io/> (canonical) and the GitHub repo
  `bmson-spec/bmson-spec`.
- **MD5**: RFC 1321
- **SHA-256**: FIPS 180-4
- **CRC-32**: IEEE 802.3, reflected polynomial `0xEDB88320`
- **SQLite**: <https://www.sqlite.org/>

## Schema (observed)

Inspect the user's existing `D:/bms/beatoraja/songdata.db` with
`PRAGMA table_info(song)` to confirm the live schema. As of this writing
the columns are (in order):

```
md5 TEXT, sha256 TEXT,
title TEXT, subtitle TEXT, genre TEXT, artist TEXT, subartist TEXT,
tag TEXT, path TEXT NOT NULL, folder TEXT,
stagefile TEXT, banner TEXT, backbmp TEXT, preview TEXT,
parent TEXT,
level INTEGER, difficulty INTEGER,
maxbpm INTEGER, minbpm INTEGER, length INTEGER,
mode INTEGER, judge INTEGER, feature INTEGER, content INTEGER,
date INTEGER, favorite INTEGER, adddate INTEGER, notes INTEGER,
charthash TEXT,
PRIMARY KEY(md5, sha256)
```

A `folder` table with `(path PK, title, subtitle, command, banner,
parent, type, date, adddate, max)` also exists; our package never
writes to it but may need to ensure it exists.

## Mode IDs (observed)

Run `SELECT DISTINCT mode FROM song` and cross-reference with file
extensions / titles. The IDs in use are:

| ID | mode_hint (BMSON) | Default key count | Player count |
|----|-------------------|-------------------|--------------|
| 5  | beat-5k           | 6                 | 1            |
| 7  | beat-7k           | 8                 | 1            |
| 10 | beat-10k          | 12                | 2            |
| 14 | beat-14k          | 16                | 2            |
| 9  | popn-5k / popn-9k | 5 / 9             | 1            |
| 25 | keyboard-24k      | 26                | 1            |
| 50 | keyboard-24k-double | 52              | 2            |

Note: pop'n shares id=9 across 5K/9K variants; the BMSON mode_hint
distinguishes them.

## Hash functions

All standard, no beatoraja-specific transforms:

- `md5(file) = standard MD5 digest of raw file bytes` (entire file as
  binary).
- `sha256(file) = standard SHA-256 digest of raw file bytes`.
- `crc32(payload) = standard reflected CRC-32, polynomial 0xEDB88320,
  initial value 0xFFFFFFFF, final XOR with 0xFFFFFFFF`. (`binascii.crc32`
  produces this directly.)

### MD5 of BMSON files

**Empirically verify**: SELECT rows where `path LIKE '%.bmson'` from the
real DB. Observe that the `md5` column is the empty string `""` for
every row. Conclusion: store `md5 = ""` for BMSON files. (Storing a
real MD5 would create a PK conflict when beatoraja next rescans.)

`sha256` for BMSON: standard SHA-256 of raw file bytes (verify with a
few SELECT samples).

## BMS file format (summary, see public spec for details)

### Text encoding
Shift-JIS, in practice the cp932 variant on Windows. Python's `cp932`
codec works. Files may include a UTF-8 BOM — strip if present.

### Headers (each begins with `#`)
- `#TITLE`, `#SUBTITLE`, `#ARTIST`, `#SUBARTIST`, `#GENRE` — text
- `#BPM <float>` — initial BPM
- `#PLAYLEVEL <int>` — displayed level (decimal)
- `#DIFFICULTY <int>` — 1=beginner, 2=normal, 3=hyper, 4=another,
  5=insane. 0 (or unset) means scanner-side inference.
- `#RANK <int>` — 0..4 (VERY HARD / HARD / NORMAL / EASY / VERY EASY)
- `#DEFEXRANK <int>` — alternative judge spec used by LR2
- `#TOTAL <float>` — gauge total
- `#STAGEFILE`, `#BANNER`, `#BACKBMP`, `#PREVIEW` — file paths
- `#LNTYPE <int>` — 1=long, 2=charge, 3=hellcharge
- `#LNOBJ <id>` — designated LN-terminator ID
- `#LNMODE <int>` — same enum as LNTYPE
- `#BASE <int>` — 36 or 62, radix for chart IDs
- `#PLAYER <int>` — 1=SP, 2=multi, 3=DP

### Per-ID definitions
- `#WAVxx <filename>` — keysound sample
- `#BMPxx <filename>` — BGA image
- `#BPMxx <float>` — BPM table entry
- `#STOPxx <int>` — STOP duration (1/192 bar units)
- `#SCROLLxx <float>` — scroll multiplier table

`xx` is two characters interpreted in `#BASE` radix.

### Conditional / random blocks
`#RANDOM N` / `#IF k` / `#ENDIF` / `#ENDRANDOM`. Implementer must
support nested random scopes. Tests can pass an explicit branch list
to make parsing deterministic.

### Chart data lines
Format: `#XXXCC:DATA` where `XXX` is the bar number (000-999) and
`CC` is the channel code.

Channels (public BMS spec):
- `01` — BGM autoplay (no judge)
- `02` — section length multiplier
- `03` — BPM change direct (hex value as 2-char id, decoded as decimal byte)
- `04` — BGA main layer
- `06` — POOR-time layer
- `07` — BGA overlay layer
- `08` — BPM change via `#BPMxx` table
- `09` — STOP via `#STOPxx` table
- `11`–`19` — P1 visible keys (1-9, with 6,8,9 sometimes scratch/aux)
- `21`–`29` — P2 visible keys (mirror P1)
- `31`–`39` — P1 invisible/hidden notes
- `41`–`49` — P2 invisible notes
- `51`–`59` — P1 long-note (LN) channels
- `61`–`69` — P2 LN channels
- `D1`–`D9` — P1 mine notes (data = damage)
- `E1`–`E9` — P2 mine notes
- `SC` (literal letters) — SCROLL change via `#SCROLLxx` table

DATA is a sequence of 2-character IDs. Length 2N implies N positions
distributed uniformly across the bar.

### Mode detection
- Default mode = BEAT_5K for `.bms` / `.bme` / `.bml`
- Default mode = POPN_9K for `.pms`
- Used-channel based promotion:
  - If channels with key index 6 or 7 are used (e.g. `18`, `19`) in P1
    lanes → upgrade BEAT_5K to BEAT_7K (and 10K → 14K)
  - If any P2 channel (`21`-`29`, `41`-`49`, etc.) is used → SP becomes
    DP (5K → 10K, 7K → 14K)
- Confirm by SELECTing diverse `.bms` / `.bme` / `.pms` files and
  cross-checking their `mode` column.

### LNOBJ
When `#LNOBJ XX` is set: any chart entry with ID `XX` is treated as the
END marker of a long-note that started at the previous non-empty entry
on the same lane. The previous entry becomes a LongNote (type from
`#LNTYPE`/`#LNMODE`); the `XX` slot itself contributes nothing further
to the visible note count.

### Timing
For a bar with cumulative section index `s_bar`, BPM `B`, and a STOP
value `T` at relative position `p` within the bar:
- bar duration at constant BPM `B`: `60_000_000 * 4 / B` microseconds
  (4 quarter-notes per bar at the standard 4/4 reading)
- Section rate `r` from channel `02` scales the bar duration by `r`.
- STOP value `T` at position `p` adds `T / 192 * (60_000_000 * 4) / B`
  microseconds at that position.
- A BPM change (ch03 or ch08) at position `p` switches the BPM used
  for positions after `p` within and after this bar.

The timeline is the ordered sequence of event positions (BPM changes,
STOPs, scroll changes, notes). Each timeline entry stores `time_us`,
`bpm`, `stop_us`, `scroll`, and the per-lane note (if any).

### Length & note count (DERIVE EMPIRICALLY)
The exact rules for the `length` and `notes` columns must be derived
by observation:
- Run `SELECT path, length, notes FROM song` on the live DB, decode a
  handful of files, and find the rule. Useful tests:
  - Does `length` count BGM-only tail time after the last visible note?
  - Are LN-end markers counted in `notes`? (Hint: try a chart with
    `#LNTYPE 1` and a chart with `#LNTYPE 2` to see if the count differs.)
  - Are mine notes counted in `notes`?
  - Are hidden notes counted?

Expected outcome (from sampling): `length` = ms of the last timeline
entry that contains any note (visible, hidden, BGM, BGA, or layer);
`notes` = count of judgement-bearing notes per the rules you derive.

## BMSON file format

See <https://bmson-spec.readthedocs.io/> for the canonical spec.
Implementation notes:

- The whole file is UTF-8 JSON. Strip leading BOM if present.
- `info.mode_hint` selects the Mode (string match against the table
  above). Unknown hint → fall back to BEAT_7K (verify empirically).
- subtitle composition: if `info.chart_name` is set, append
  `"[<chart_name>]"` to subtitle (with one space separator when subtitle
  was non-empty).
- Notes live in `sound_channels[].notes[]`. Fields:
  - `x` — lane index (0-based; check the spec for the mapping to
    BMS-style lanes)
  - `y` — position in ticks (resolved against `info.resolution`)
  - `l` — length in ticks; 0 means a normal note, >0 means LN
  - `t` — LN type override (1=long, 2=charge, 3=hellcharge)
  - `up` — boolean; meaning is part of BMSON spec
- Mines live in `mine_channels[].notes[]` with `damage` field.
- Hidden notes in `key_channels[]`.
- `bpm_events`, `stop_events`, `scroll_events`, `lines` (bar lines)
  carry timing.
- `info.judge_rank` is a percentage (BMSON convention) — store directly
  as `judge` (subject to "judge transform" below).
- `info.total` is a percentage — handle per "total transform" below.
- LN type fallback: when a note has `t = 0` (unset), use
  `info.ln_type` (which itself may be 0 → UNDEFINED).

## SongRow population

| Column | Source |
|--------|--------|
| `md5` | raw-file MD5 (`""` for `.bmson`) |
| `sha256` | raw-file SHA-256 |
| `title`, `subtitle`, `genre`, `artist`, `subartist` | parsed headers |
| `tag` | empty `""` (user-level metadata, not from the chart) |
| `path` | absolute file path with OS-native separators |
| `folder` | CRC-32 of `<absolute parent dir>` + `b'\\\0'` (literal backslash + NUL) **— but see "Sibling inheritance" below** |
| `parent` | CRC-32 of grandparent dir, same algorithm |
| `stagefile`, `banner`, `backbmp`, `preview` | parsed headers (paths) |
| `level` | parse `#PLAYLEVEL` as int, default 0 |
| `difficulty` | `#DIFFICULTY` if non-zero, else inference fallback (see below) |
| `maxbpm` / `minbpm` | int(max/min BPM across initial + all BPM changes) |
| `length` | per the empirical rule from BMS section above |
| `mode` | Mode.id |
| `judge` | result of the judge transform (empirically derived, see below) |
| `feature` | bit flags — see "feature bits" below |
| `content` | bit flags — see "content bits" below |
| `date` | int(file mtime), epoch seconds |
| `favorite` | 0 for new rows; preserved on upsert (see "UPSERT" below) |
| `adddate` | int(current time) for new rows; preserved on upsert |
| `notes` | per the empirical rule |
| `charthash` | `""` — we don't compute this (see "Things we don't compute") |

### Feature bits (DERIVE EMPIRICALLY)

`feature` is a bitfield. The exact bits must be derived by inspecting
diverse rows in the live DB:

1. Pick rows that you know have specific features from their files:
   - A chart with `#STOPxx` channels → feature has bit X
   - A chart with `#SCROLLxx` channels → bit Y
   - A chart with `#LNTYPE 1` (normal LN) → bit Z
   - A chart with `#LNTYPE 2` (charge) → bit W
   - A chart with `#LNTYPE 3` (hellcharge) → bit V
   - A chart with unspecified LN type but LN channel data → bit U
   - A chart with mine notes (D-channel) → bit T
   - A chart with `#RANDOM` blocks → bit S
2. From the observed `feature` values, deduce the bit assignments.

Expected outcome (likely): bits at positions 0..7, ordered something
like `UNDEFINEDLN, MINENOTE, RANDOM, LONGNOTE, CHARGENOTE,
HELLCHARGENOTE, STOPSEQUENCE, SCROLL`. **Confirm by observation, not
assumption.**

### Content bits (DERIVE EMPIRICALLY)

Same approach:
- Folders containing a `.txt` file → which content bit?
- Charts with BGA images → which bit?
- "No-keysound" charts (length ≥ 30s but very few WAV defs) → which bit?
- Charts with a `preview*.{wav,ogg,mp3,flac}` file in the folder → which bit?

### Judge transform (DERIVE EMPIRICALLY)

The `judge` column is **not** the raw `#RANK 0..4`. It's a percentage
value the runtime computes.

Derivation procedure:
1. SELECT a sampling of rows with their `mode` and `judge` values.
2. Parse the source BMS files for their `#RANK` (or `#DEFEXRANK`).
3. Cross-reference: for each (mode, raw rank) pair, observe what
   `judge` value is stored.
4. Note that POPN modes (id=9) likely use a different window than
   BEAT/KEYBOARD modes.

Suggested SQL to help:

```sql
SELECT mode, judge, COUNT(*) FROM song
WHERE judge > 0
GROUP BY mode, judge
ORDER BY mode, judge;
```

You should see clusters of fixed percentages per mode. Match those
clusters against the source BMS `#RANK` values.

`#DEFEXRANK V` → `judge = V * baseline / 100` where baseline is the
NORMAL (#RANK 2) percentage for that mode.

BMSON `judge_rank` (percent) → store directly if > 0, else 100.

### Total transform (DERIVE EMPIRICALLY)

Likewise the `total` value in a BMS file undergoes a transform before
being stored (or used). The default-total formula depends on mode and
note count. SELECT samples and reverse-engineer. If too complex, omit
— this column isn't in the `song` table directly; it's only consumed
during `charthash` computation, which we skip.

### Difficulty inference fallback

When `#DIFFICULTY` is 0:
1. Scan `subtitle` for English keywords: `beginner`, `normal`, `hyper`,
   `another`, `insane`, `leggendaria` — map to 1..5.
2. Failing that, scan `title + subtitle` for the same keywords.
3. Failing that, infer from `notes`:
   - <250 → 1, <600 → 2, <1000 → 3, <2000 → 4, else 5
   (Derive the thresholds by sampling.)

## INSERT logic

### Sibling inheritance (this is OUR design, not from beatoraja)

beatoraja's runtime uses Java's default-charset `String.getBytes()`
when CRC-ing path strings. On the user's machine that default isn't
something we can easily reproduce from Python (we've tested
`cp932` / `utf-8` / `mbcs` / `shift_jis_2004` / `utf-16` and many
others — none match for non-ASCII paths). Our solution:

When upserting a row, look for any existing row in the same directory
and copy its `folder` and `parent` values onto the incoming row:

```sql
SELECT folder, parent FROM song WHERE path LIKE ? LIMIT 1
```
where the parameter is `<directory>\%`.

If found, the inherited values win. If not (brand-new directory), use
our computed values; beatoraja will rebuild on next scan if it
disagrees.

### UPSERT

`INSERT OR REPLACE INTO song(...) VALUES(?, ?, ..., ?)` with composite
PK `(md5, sha256)`.

**Storage-affinity trap (verified empirically)**: SQLite's type
affinity for TEXT columns is determined per-value by the Python type
you bind. The runtime reads back rows with `WHERE md5 = 'hex-string'`
(TEXT-affinity comparison), so if you bind ``bytes`` for the text
columns SQLite stores them as BLOB and the runtime's SELECT silently
misses them (the runtime treats the chart as "not installed" and
greys it out in the selector). Pass Python ``str`` for every TEXT
column (md5, sha256, title, path, ...) so SQLite assigns TEXT
affinity. Pass Python ``int`` for INTEGER columns. Do **not** call
``str.encode()`` before binding.

Preserve `favorite` and `adddate` on PK collision:
```sql
SELECT favorite, adddate FROM song WHERE md5=? AND sha256=?
```
If the incoming row's value is 0 and an existing row has non-zero,
keep the existing. Default `adddate` to current epoch seconds when
both are 0.

### Schema migration

If `song` or `folder` table is missing (fresh DB), create with the
schema above. If columns are missing (legacy DB), `ALTER TABLE ... ADD
COLUMN` to bring them in line. **Never drop existing tables.**

## Things we do NOT compute

### charthash
This is a runtime-specific SHA-256 of an internal chart serialization.
The serialization format isn't part of any public BMS spec.

Storing `""` is safe: the `charthash` column is used by the runtime
for duplicate-chart detection across moves/renames. Without it, a
rename causes the chart to be re-indexed as new — minor inconvenience,
not a correctness issue.

### feature/content edge bits
If empirical derivation can't pin down every bit (e.g. the
`NOKEYSOUND` bit's exact trigger), leaving that bit as 0 is
acceptable; beatoraja's behavior degrades gracefully.

## CLI

```
python -m scripts.songdb \
  --songdata-db PATH \
  --music-root PATH \
  --paths FILE [FILE ...]
```

Or, integrated with the parent skill's state directory:

```
python -m scripts.songdb \
  --songdata-db PATH \
  --music-root PATH \
  --from-state-dir DIR
```

The `--from-state-dir` mode reads `<dir>/results.csv` (output of the
parent `install_diffs.py`). Each row's `placed` column is a
**semicolon-separated** list of basenames (because one差分 archive can
ship multiple `.bms` files for different difficulties). Split on `;`,
strip whitespace, and join with `<music-root>/<top_folder>/<basename>`
for each piece. Then upsert.

## Module layout (suggested)

Implementer's choice, but a reasonable split:

- `hashing.py` — MD5, SHA-256, CRC-32 helpers
- `model.py` — data classes: TimeLine, Note variants, BMSModel, etc.
- `mode.py` — Mode constants
- `parser_bms.py` — BMS / BME / BML / PMS parser
- `parser_bmson.py` — BMSON parser
- `songdata.py` — BMSModel → SongRow projection (judge transform,
  feature/content bits, difficulty inference, filesystem signals)
- `writer.py` — SQLite schema management, sibling inheritance, UPSERT
- `__main__.py` — CLI
- `_golden_test.py` — verification harness

## Verification

Compare against the live `D:/bms/beatoraja/songdata.db` over a random
1000-row sample (seed=99 reproducibly). Target match rates:

- `md5`, `sha256`: 100% (no transform, standard hashes)
- `title`, `artist`, `genre`, `subtitle`, `subartist`: ≥99%
- `level`, `difficulty`, `mode`: 100% for files with explicit fields
- `folder` / `parent`: from build_song_row — likely ~0% on non-ASCII
  paths (the JVM-charset issue), but `folder_writer` / `parent_writer`
  (after sibling inheritance at upsert time) should hit 100%.
- `notes`, `length`, `maxbpm`, `minbpm`: ≥99% (rare edge cases on
  randomized charts or extreme BPM values)
- `judge`: ≥97% (a few percent will be stale rows from older
  runtime versions whose transform has since shifted)
- `feature`, `content`: ≥99% if bits are derived correctly
- `charthash`: 0% — we don't compute it
