Doom Bot Arena is a spectator app for watching eight AI bots with distinct personalities play a first-person deathmatch, presented like a live esports broadcast. This system holds the shared tokens, the spectator primitives (feed tile, minimap, scoreboard, timeline, telemetry) and three layout directions, A, B and C, each shown on desktop and phone, all frozen on the same match snapshot so they can be compared directly.

## The three directions

| | Idea | Trade-off |
|---|---|---|
| **A · Broadcast Multiviewer** (dark) | A zero-waste 4×2 wall of equal 4:3 feeds, like an OB-van production monitor; standings ride a data strip on top, and the timeline, kill feed and pinned map sit in the aspect-ratio margin below. Only tile state pulls the eye. | Most faithful to how directors watch and the densest, but the map is small, so you read spatial context from the map rather than from the layout. |
| **B · Spatial Arena Ring** (dark) | The wireframe map is the anchor; feeds flank it by the sector their bot occupies, north to south, tethered to their dots. Focusing a bot swaps its feed into the centre and pins the map bottom-right. | Spatial story is free (you see the third party coming), but feeds are smaller and shift as bots move. |
| **C · Swiss Editorial Telemetry** (paper) | A warm paper results sheet: ink rules, grotesque numerals, a full-height standings column and a 2×4 grid where each feed is led by a bold label block. | Most legible and recap-friendly, but feeds are smallest and a light ground is harsh in a dark room. |

## Snapshot (ground truth for every screen)

Clock `02:14` of `05:00`. Sorted by frags:

| ID | Callsign | Name | Personality | F | D | HP | Weapon | State |
|---|---|---|---|---|---|---|---|---|
| 01 | VEX | Vex | Rusher | 14 | 4 | 22 (critical) | Shotgun | In combat, dueling KRN in the Central Hall |
| 02 | KRN | Kronos | Tactician | 11 | 3 | 38 | Chaingun | In combat, dueling VEX |
| 03 | NUL | Null | Ambusher | 9 | 6 | 100 | Rocket Launcher | Stalking |
| 04 | ASH | Ash | Berserker | 8 | 9 | 0 | — | Dead, respawn in 2.4s |
| 05 | ORB | Orbit | Kiter | 7 | 5 | 85 | Plasma Rifle | Roaming |
| 06 | HEX | Hex | Camper | 6 | 2 | 100 | Rail/Sniper | Holding angle |
| 07 | PYR | Pyre | Flanker | 5 | 11 | 64 | Super Shotgun | Collecting armor |
| 08 | ZED | Zed | Erratic | 2 | 12 | 90 | Chainsaw | Hunting |

`DBA.BOTS` and `DBA.EVENTS` (62 frags in five bursts) carry this data; every screen reads from them.

## Content fundamentals

- Write like an observer's tool, not a marketing page: short, uppercase, factual labels (`IN COMBAT`, `HOLDING ANGLE`, `RESPAWN IN 2.4s`, `ENGAGING 02 KRN`).
- Name a bot as `ID CALLSIGN` in data (`01 VEX`) and `ID Name` on nameplates (`01 Vex`). Always two-digit IDs.
- Times are `MM:SS`; respawn countdowns carry one decimal and a small `s`. Frags and deaths take a small trailing `F` / `D`.
- Every numeral uses `tabular-nums` (the `.dba` root sets it); never let a column jitter.
- No emoji. Glyphs allowed: `▸` (target), `✕` (frag), `⤢` (expand), `↘` (collapse), `←` (return), `↗` (on stage).
- Never use the name "Doom" as a logo, and never show game artwork, screenshots, emblems or fonts imitating a game's branding. The wordmark is plain type.

## Visual foundations

**Themes.** `dark` (Broadcast Dark) for A and B; `paper` for C. A screen pins its theme with `data-theme` on its root; primitives follow the page theme.

**Surfaces.** `surface-0` ground → `surface-1` panels and tile chrome → `surface-2` raised rows. Feeds are `feed` with `feed-grid` lines: a flat, muted 4:3 rectangle with a technical grid, corner brackets and a centre crosshair. Nothing else goes inside a feed.

**Bot palette.** Eight `oklch()` hues, `bot-01` … `bot-08`, tuned per theme: lighter and more chromatic on dark, darker on paper. All exceed 4.5:1 on `surface-0` and `surface-1` in both themes, and `on-bot` text exceeds 4.5:1 on every bot fill. Neighbouring hues are also split by lightness (`bot-02` vs `bot-06`, `bot-01` vs `bot-07`). Use a bot colour only to identify that bot: swatch, HP bar, map dot and cone, frag tick, and the In Combat frame. Never use it as a decorative accent.

**Pre-attentive states.** Do not give all eight tiles the same weight.
- Normal: 1px `rule` frame, transparent nameplate, `ink-faint` crosshair.
- In Combat: the frame becomes the bot colour (border plus a 1px hard outer spread, no blur), the nameplate fills with the bot colour with `on-bot` text, and the crosshair turns the bot colour.
- Critical HP (≤25): HP value in the bot colour and an inverted `CRIT` chip (`ink` on `surface-0`).
- Dead / Respawning: dashed `rule-strong` frame, everything drops to `ink-faint`, and the feed is replaced by a countdown with a progress bar and the fragger.

**Type.** `--font-display` Barlow Condensed for nameplates, names and kickers (uppercase, tracked). `--font-mono` IBM Plex Mono for all data, clocks and labels. `--font-grotesk` Archivo (900, expanded) for Option C's masthead, numerals and standings. Styles: `bc-*` broadcast, `data-*` data, `ed-*` editorial. Faces are hosted on Google Fonts; previews link them.

**Shape.** Square everything: `radius-0`. `radius-1` is only for keycap hints; `radius-dot` only for map dots. No shadows, gradients, glows, glass or rounded cards. Hierarchy comes from rules (`stroke-hair`, `stroke-hot`, `stroke-heavy`), fills and type weight.

**Spacing.** Option A uses `space-2` gutters and a `space-3` frame (dense). B uses `space-5` gutters. C uses `space-6` gutters and frame (generous).

**Layout rules.**
- Feeds are always 4:3 (`aspect-ratio`), never cropped or stretched.
- All eight feeds are visible by default. Focusing promotes one feed and keeps the other seven one click away.
- The minimap is pinned bottom-right in 8-up and focused views and always sits in its own layout cell (A: the dock, B focused: beside telemetry, C: foot of the rail, phones: a fixed dock). A scroll region ends above the dock, so the map never covers a nameplate or HP bar. Option B's 8-up view is the one exception: there the map is the centre.
- Focus ring: solid 2px `focus`, 2px offset.

**Motion.** Not specified for the static comps. In product: map dots interpolate at the tick rate, and the In Combat state appears instantly with no fade or pulse.

## Iconography

There is no icon set. Use the glyphs listed above in `--font-mono`, swatches (9px squares) for identity, and keycaps (`<kbd>`) for shortcuts: `ESC` return to multiview, `M` map, `1`–`8` focus a bot.

## Components

`DBA.tile`, `DBA.ctile`, `DBA.mapPanel`, `DBA.scoreStrip` / `scoreTable` / `scoreColumn`, `DBA.timeline`, `DBA.killfeed`, `DBA.telemetry`, `DBA.clock`, and `DBA.tethers` (B) all return HTML strings, styled by `components/bundle.css`. Wrap output in an element with class `dba`.
