---
name: bms-diff-install
description: Install or synchronize BMS差分 from a beatoraja-compatible difficulty table, optionally download missing parent songs, and place charts into matching music folders without overwriting existing files. Use for 難易度表 URL, header.json, url_diff, or beatoraja差分 installation requests; do not use for ordinary BMS library organization unrelated to a difficulty table.
---

# Install BMS差分

Install chart differentials from a beatoraja-compatible difficulty table. Use
the bundled scripts for deterministic downloading, parsing, matching, and file
placement. Reserve model judgment for cases the matcher marks `ambiguous`.

## Resolve the skill root

Treat the directory containing this `SKILL.md` as `SKILL_ROOT`. Resolve every
script to an absolute path under `SKILL_ROOT/scripts`; do not assume the skill
is installed at a fixed user-home path. Use an available Python 3.10+ command
(`python`, `python3`, or `py -3`) consistently throughout the run.

Important scripts:

- `install_diffs.py`: dry-run and deterministic placement pipeline
- `install_parents.py`: missing-parent downloader
- `prepare_review_input.py`: reduce ambiguous cases for model review
- `apply_review.py`: validate and apply reviewed placement decisions
- `report.py`: create the consolidated unrecovered report
- `scripts/songdb`: optional direct registration into `songdata.db`

The older `prepare_haiku_input.py` and `apply_haiku.py` files are compatibility
helpers. Do not use them for new Codex runs.

## Authorization boundaries

- A user request to install or synchronize a table authorizes the complete
  in-scope workflow: dry-run, parent downloads, deterministic placement,
  reviewed placement, and report generation. Continue through those phases
  without pausing for repeated confirmation. Report counts and download-size
  estimates as progress updates instead.
- Honor narrower requests such as "dry-run only", a host/MD5 filter, or
  "differences only". Do not expand beyond the table, paths, and filters the
  user placed in scope.
- Parent packages are often 50–150 MB each. Resolve/deduplicate them with
  `install_parents.py --dry-run` and report the unique count and estimated
  range, but do not stop solely to request another approval.
- Never overwrite an existing music file. The bundled placement scripts enforce
  this; do not bypass them with direct copies.
- For a full install/synchronize request, register placed charts in
  `songdata.db` as the final step when beatoraja is not running. Create a
  timestamped backup first and do not pause for another confirmation. Skip the
  direct DB write when the player is running or the user requested files-only,
  dry-run-only, or no database changes; use beatoraja's rescan in those cases.
- Keep reusable downloads and logs in `STATE_DIR`. Do not clear caches unless
  the user explicitly requests it.

## Gather inputs

Discover what can be inferred locally before asking the user. Required values:

- `HEADER_URL`: difficulty-table `header.json`. If given a landing page, read
  its `<meta name="bmstable" content="...">` and resolve that value relative to
  the page URL.
- `SONGDATA_DB`: beatoraja's `songdata.db`.
- `MUSIC_ROOT`: directory containing folders such as `[ARTIST] TITLE`.
- `STATE_DIR`: persistent working directory, defaulting to
  `<beatoraja-install>/_install_diff/state` when appropriate.

Verify that `SONGDATA_DB` and `MUSIC_ROOT` exist and that `STATE_DIR` resolves
to the intended beatoraja workspace before continuing.

## Run the workflow

### 1. Dry-run the table

```text
python <SKILL_ROOT>/scripts/install_diffs.py \
  --header-url <HEADER_URL> \
  --songdata-db <SONGDATA_DB> \
  --music-root <MUSIC_ROOT> \
  --state-dir <STATE_DIR> \
  --dry-run
```

Use arguments appropriate to the active shell; the block above is schematic.
The first run can take 5–10 minutes for a large table. Later runs reuse
`STATE_DIR/downloads`. The table is refreshed each run and falls back to its
cache on fetch failure; use `--no-refresh-table` only for intentional offline
or pinned-table work.

Report these counters:

- `auto_dry`: deterministic placements ready to apply
- `ambiguous`: cases requiring model review
- `no_parent`: parent song is not installed
- `bundled_in_parent`: chart exists only in the parent package
- `error`: download or parse failure

`bundled_in_parent` always requires the parent-install phase, even when the
user originally asked for differences only. Treat those packages as part of
installing the requested charts unless the user explicitly forbids parent
downloads.

### 2. Optionally install missing parents

Run this when the user asked for parents or `bundled_in_parent` is nonzero.
First resolve URLs without downloading:

```text
python <SKILL_ROOT>/scripts/install_parents.py \
  --state-dir <STATE_DIR> \
  --music-root <MUSIC_ROOT> \
  --dry-run
```

Rerun without `--dry-run`. Filters `--limit`, `--host`, and
`--md5` may be used to keep a large operation within the user's requested
scope. Existing folders are merged by adding only missing files.

If `parent_install_log.csv` contains `needs_browser`, read
[references/parent-resolution.md](references/parent-resolution.md). After any
parent installation, rerun step 1 so the new parents participate in matching.

### 3. Apply deterministic placements

Apply the reported `auto_dry` placements without an extra confirmation pause:

```text
python <SKILL_ROOT>/scripts/install_diffs.py <same arguments> --apply
```

This writes only deterministic `auto` cases and never overwrites existing
files. It leaves ambiguous, missing-parent, and error cases untouched.

### 4. Review ambiguous placements

If `ambiguous` is nonzero, read and follow
[references/ambiguous-placement.md](references/ambiguous-placement.md). Use a
`gpt-5.6-luna` subagent for the bounded batch judgment, with
`fork_turns: "none"`; supply the absolute input/output paths and all decision
rules in its prompt. The subagent must only write `review_decisions.json`, not
place files. If subagents are unavailable, perform the same review directly.

When the requested scope includes missing parents and a review case is skipped
because none of its candidate folders match the chart metadata, treat it as a
likely false-negative `no_parent`. Make one parent-recovery attempt using that
entry's table `url`, rerun the deterministic matcher, and review the case again.
Do not retry the same chart or parent URL indefinitely; after one completed
parent-recovery pass, preserve a remaining skip in the final report.

### 5. Rescue remaining diff-download errors

For GDrive folder URLs in `errors.jsonl`, enumerate the folder, download the
chart file or a ZIP containing the relevant chart-side files into
`STATE_DIR/downloads/<md5>.bin`, then rerun `install_diffs.py --apply --md5
<hash>` only after placement authorization. Report dead or unsupported links
without fabricating a successful resolution.

### 6. Register charts for player visibility

For a full installation, check that beatoraja is not running, create a
timestamped backup of `songdata.db`, and run:

```text
python -m scripts.songdb \
  --songdata-db <SONGDATA_DB> \
  --music-root <MUSIC_ROOT> \
  --from-state-dir <STATE_DIR>
```

Run from `SKILL_ROOT` so the module resolves correctly. This registers
deterministic and reviewed placements from the state logs and preserves
existing favorites/add dates on key collisions. When the player is running or
database writes were excluded from scope, leave the DB untouched and use
beatoraja's F5 / 楽曲データベース更新 or `updatesong` instead.

### 7. Report

```text
python <SKILL_ROOT>/scripts/report.py --state-dir <STATE_DIR>
```

Report total placements, remaining categories, and absolute paths to
`unrecovered.md` and `unrecovered.csv`. State whether `songdata.db` was updated
or a beatoraja rescan remains necessary.

## Operational invariants

- `AUTO_RATIO=0.95` and `AUTO_GAP=0.20` protect against false positives. Do
  not widen them to force more automatic matches.
- Tied full-hit candidates often use generic sample names; artist/title fit is
  more important than hit count in those cases.
- Entries without a usable `url_diff` are `bundled_in_parent`; they cannot be
  recovered by ambiguous placement review.
- BMS text commonly uses Shift-JIS. State JSON/CSV and all skill-maintained text
  remain UTF-8.
- `.rar`, `.7z`, and `.lzh` extraction requires 7-Zip; `.zip` uses Python's
  standard library.
- A repeated run is expected and reuses both diff and parent caches.
