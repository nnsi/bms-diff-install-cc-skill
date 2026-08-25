# Parent URL resolution

Read this reference only when `parent_install_log.csv` contains rows with
status `needs_browser` (or the legacy status `needs_haiku`).

## Luna static-resolution pass

Give a `gpt-5.6-luna` subagent (`fork_turns: "none"`) the absolute path to
`parent_install_log.csv`, the absolute output path below, and the selection
rules in this section. It may inspect static pages, but it must not download
parent archives or modify `MUSIC_ROOT`.

For each unresolved `parent_url`, locate the parent/full BMS package. Prefer
links labelled 本体, Body, Full, or the only/largest BMS archive. Reject links
labelled 差分, sabun, diff, or BGA only.

Write a UTF-8 JSON object to `STATE_DIR/parent_overrides.json`:

```json
{
  "https://original.example/page": "https://download.example/song.zip",
  "https://dead.example/page": null
}
```

Then route successful replacements through the existing adapters:

```text
python <SKILL_ROOT>/scripts/install_parents.py \
  --state-dir <STATE_DIR> \
  --music-root <MUSIC_ROOT> \
  --overrides <STATE_DIR>/parent_overrides.json
```

Report parent download counts and size estimates as progress. An in-scope
installation request already authorizes the download phase described in
`SKILL.md`.

## Terra interactive-resolution pass

For unresolved pages requiring JavaScript, clicking, session cookies, or a
browser download event, spawn a `gpt-5.6-terra` subagent with
`fork_turns: "none"`. Give it only the unresolved cases, this reference, and
absolute state paths. It must use the available Codex browser-control skill and
follow that skill before interacting with the page. Prefer the selected
browser's built-in Playwright surface; do not install a separate Playwright
runtime into `STATE_DIR`.

Useful host-specific techniques:

- Wayback: query the CDX API for successful snapshots, prefer a plausible large
  archive response, and use `https://web.archive.org/web/<timestamp>id_/<url>`
  for raw bytes. Verify the response is an archive rather than an HTML stub.
- MediaFire: wait for the real download control and extract or activate its
  resolved download URL.
- getuploader: establish the listing-page session, submit any agreement form in
  the same browser context, and capture the download.
- Dropbox `scl/fi`: preserve `rlkey`; when direct normalization returns 400,
  activate the page's actual Download control in the same session.
- GDrive `/drive/u/N/folders/<ID>`: normalize it to
  `/drive/folders/<ID>` before passing it back to the adapter.
- Dead AXFC links: inspect the source page for an alternate host. Emit `null`
  when the dead link is the only source.

Prefer a replacement URL in `parent_overrides.json`. If browser control can
only save the actual archive, keep it inside the scoped state cache and record
exactly how the cached file maps to the original parent URL. Never label an
HTML page or an unverified tiny response as a recovered archive.
