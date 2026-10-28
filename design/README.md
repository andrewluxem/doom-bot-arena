# design/

Spectator UI directions for Doom Bot Arena: shared tokens, a small renderer, and three layout options (A Broadcast Multiviewer, B Spatial Arena Ring, C Swiss Editorial Telemetry), each on desktop and phone, all on the same frozen match snapshot.

Open `index.html` in a browser for the gallery (needs a local server so the iframes load: `npx serve design` or `python3 -m http.server -d design`).

| Path | What |
|---|---|
| `tokens.json` | Source of truth: colors (dark + paper themes, 8 `oklch()` bot colors), type, spacing, radius, stroke |
| `tokens.css` | CSS custom properties generated from `tokens.json` |
| `dba.css` / `dba.js` | Component styles and renderer (`window.DBA`: tile, ctile, mapPanel, scoreStrip/Table/Column, timeline, killfeed, telemetry, tethers) plus the snapshot data |
| `screens/*.html` | One page per screen and primitive |
| `components/*.md` | Notes per screen/component |
| `SYSTEM.md` | Brand book: content rules, visual foundations, state rules, layout rules |

Fonts load from Google Fonts (Barlow Condensed, IBM Plex Mono, Archivo).
