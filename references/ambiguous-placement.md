# Ambiguous placement review

Use this procedure only for rows classified `ambiguous` by `install_diffs.py`.
Do not review or rewrite deterministic `auto` decisions.

## Prepare review input

```text
python <SKILL_ROOT>/scripts/prepare_review_input.py --state-dir <STATE_DIR>
```

This writes UTF-8 JSON to `STATE_DIR/review_input.json`. Each case contains:

- `md5`: stable identifier
- `table_title`, `table_artist`: difficulty-table metadata
- `bms_title`, `bms_artist`: chart metadata; usually more authoritative
- `total_wavs`: number of referenced keysounds
- `candidates`: up to ten exact folder basenames with keysound hit counts;
  strong metadata matches are retained ahead of generic full-hit ties

## Luna task

Spawn a `gpt-5.6-luna` subagent with `fork_turns: "none"`. Give it this file,
the absolute path to `review_input.json`, the absolute output path
`review_decisions.json`, and the rules below. The task is read-one/write-one:
the agent must not run placement scripts or change `MUSIC_ROOT`.

Write exactly one decision for every input case to
`STATE_DIR/review_decisions.json`:

```json
[
  {
    "md5": "...",
    "decision": "place",
    "folder": "[ARTIST] TITLE",
    "reason": "artist and base title match"
  },
  {
    "md5": "...",
    "decision": "skip",
    "folder": null,
    "reason": "none of the candidate artists match"
  }
]
```

Decision rules:

1. Require a convincing artist/title relationship. Chart titles often add a
   terminal difficulty or remix suffix such as `[Eternity]`; compare the base
   title too.
2. When several candidates tie at full hits, treat the hit counts as
   uninformative and judge the folder basename against artist/title metadata.
3. Account for fullwidth punctuation, spacing, bracket variants, and clearly
   reversed or obfuscated names.
4. A large top-vs-second hit gap supports a placement only when the metadata is
   also compatible.
5. When two folders both legitimately match, prefer the simpler/original song
   folder over a derivative folder.
6. If evidence is weak or no candidate artist/title matches, use `skip`. Never
   fall back to the first or highest-scoring candidate merely to produce a
   placement.
7. For `place`, copy `folder` verbatim from `candidates[].folder`. Do not emit a
   path, corrected spelling, or newly invented folder.

## Validate, summarize, and apply

Before applying, validate that the output has one unique decision per input
case, only `place`/`skip` values, and no folder outside that case's candidate
list. `apply_review.py` repeats these checks and rejects invalid rows.

Report the proposed `place` and `skip` counts as a progress update, then apply
them without another confirmation pause when the user requested installation:

```text
python <SKILL_ROOT>/scripts/apply_review.py \
  --state-dir <STATE_DIR> \
  --music-root <MUSIC_ROOT>
```

Report its `placed`, `skipped`, and `errors` counters and retain
`review_apply_log.csv` as the audit log.

## Recover likely missing parents

When the user requested missing-parent installation, a `skip` whose reason says
that none of the candidates match is evidence that the real parent may be
absent even though the keysound ratio exceeded the deterministic no-parent
threshold. For each such chart, use its difficulty-table `url` in one parent
installation pass, rerun `install_diffs.py`, and regenerate the review input.
Apply a newly deterministic or convincing placement. Stop after one recovery
attempt per chart/parent URL and report any case that still does not match.
