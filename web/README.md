# web/: spectator viewer (Option A)

A static site that replays a match bundle (`docs/MATCH_FORMAT.md`, schema 1 or 2): eight POV videos and the overhead map on one clock, standings, frag timeline, kill feed, single-bot focus. Vite and vanilla JS. `design/tokens.css` and `design/dba.css` are imported unchanged; `src/render.js` ports the `dba.js` renderers to take live per-tic state.

```
npm install
npm run dev            # http://localhost:5173
npm run build          # dist/: index.html, assets/, index.json, sample/
python3 -m http.server -d dist 8000   # any static server works
```

**Vercel:** set the project root to `web/`. `vercel.json` sets the build command and `dist` output.

## Bundles

`public/index.json` lists `{id, map, iwad, date, duration_tics, path}` per bundle; the viewer opens the one named in the URL hash, else the first. To add one:

```
python3 tools/export_sample.py bundles/<id>     # from the repo root; run it in the match image for the same ffmpeg
```

The exporter copies the bundle (with `spots.json` for schema 2) to `public/sample/<id>/` and rewrites `index.json`, newest first, so the viewer opens the newest bundle by default. If the copy is over 25 MB, it re-encodes the POV videos at crf 32 and records the change in `match.json.sample_reencode`, with the original hashes. The copy still passes `engine/validate_bundle.py`.

## Controls

| | |
|---|---|
| `1`–`8`, click a tile or a map marker | focus that bot |
| `M`, click the map | expand or collapse the map |
| `ESC` | back to the multiview (or collapse the map) |
| `Space` | play or pause |
| `←` `→` (`Shift`: 1 s) | step one tic |
| timeline | click a tick to seek to that frag; drag to scrub |

The hash is `#b=<bundle id>&f=<slot>&t=<bundle tic>&m=1`. Tics before `frame0_tic` clamp to 00:00.

## Timing

`src/clock.js` is the only clock. It runs at `match.json.ticrate`, starts at `frame0_tic` (displayed 00:00), and play, pause, seek and scrub all go through it.

`src/sync.js` points each video at `(tic - frame0_tic) / ticrate`:
- drift over 0.35 frames is trimmed with `playbackRate`;
- drift over 2 frames is re-seeked;
- playback waits until all eight are parked on the same frame.

Drift is measured on presented frames (`requestVideoFrameCallback`). When playback reaches the end, the console prints the maximum drift for each video.

Servers without byte ranges (`python3 -m http.server`) cannot seek MP4s. The viewer detects this and loads the videos whole.

## Acceptance

```
npm run build && npm run acceptance -- --bundle 20261028T042416Z-MAP01-s1234 --event 473   # milestone 1B sample
npm run acceptance -- --bundle <milestone 2 sample id>                                       # schema 2 sample
# Google Chrome; serves dist/ with python3 -m http.server
python3 scripts/contrast.py public/sample/<id>       # numpy + ffmpeg
```

`scripts/acceptance.mjs` checks:
- drift for the whole sample;
- 16 paused and playing seeks;
- one frag event on the map, in the kill feed, in the killer's tile, and in the killer's POV frame (pixel-matched against ffmpeg's decode);
- the keys and clicks without a reload;
- the 390x844 phone layout;
- on a schema 2 bundle, the focused bot's telemetry shows its selected weapon by name, its target and its sector.

`scripts/contrast.py` measures tile text over every frame of the eight videos.

## What it shows, and where it differs from the design

- **Nameplates sit on the video.** The nameplate and HP footer overlay the 4:3 video on a flat `surface-0` 86% scrim, instead of sitting in bands above and below it.
  - Text over video uses `ink` or `ink-muted` only: `ink-faint` is 4.1:1 on dark `surface-0`.
  - Critical HP keeps the bot-coloured value, on an opaque chip.
- **Dead tiles** dim and desaturate the running video under the countdown. The countdown is computed from the death tic and `respawn_delay_s`.
- **Map orientation.** The map is rotated 0 or 90 degrees, whichever fits its panel larger (MAP01 is taller than wide). Cones and labels use the same transform.
- **Kill feed and timeline** show every death at or after `frame0_tic`: frags, telefrags, suicides (`SELF`) and environmental deaths (`ENV`). Without them, the D column would rise with no row to explain it. Pre-roll events are never shown.
- **Standings** apply `match.json.score.rule` to the events up to the current tic; at the last tic they must equal `score.rows`. Ties keep the previous order.
  - Engine counters (`frags_raw`, `deaths_raw`) are recorded only at the last tic, so the hover text and telemetry label them "at end".
- **Schema 2 fields** (milestone 2A bundles):
  - **Weapon** is the bot's own selection (`weapon_name`), on the tile footer and in telemetry, with the engine slot beside it. The selection can lead the slot by the switch time. With no selection (no allowed weapon with ammo), telemetry shows the slot's weapon marked `NOT SELECTED`.
  - **Target** is `target_slot`: the player the bot is engaging, with its colour swatch, id and code.
  - **Sector** is the engine sector id, with its floor height from `map.json`. The design's named zones do not exist in the data, so none are shown.
  - **Spot**: for a bot holding a perch or ambush spot (`spots.json` `chosen`), telemetry shows its position and watch angle, and the map draws a dashed square with a tick toward the watch angle.
- **Not recorded, so not shown:**
  - which weapon is held in slots 1 and 3 in schema 1 bundles (`SG/SSG`, `FIST/SAW`);
  - per-tic engine counters;
  - the design's "DUEL ▸ 02 KRN" style tags.
