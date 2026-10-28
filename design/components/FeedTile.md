# FeedTile

A bot's first-person feed: a 4:3 flat placeholder (320×240 ratio enforced with `aspect-ratio`) with a nameplate (`[ID] Name`, swatch, frags) and a compact HP bar.

Render with `DBA.tile(id, {size})` (A/B) or `DBA.ctile(id, {stack})` (C). The consumer provides the bot ID; state comes from the snapshot data.

## States (never style all eight alike)
- **Normal:** 1px `rule` border, transparent nameplate, `ink-faint` crosshair. Quiet on purpose.
- **In Combat:** border and a 1px outer spread in the bot colour (2px total, no blur) and a nameplate filled with the bot colour, text in `on-bot`. Crosshair marks switch to the bot colour.
- **Critical HP (≤25):** HP value in the bot colour plus an inverted `CRIT` chip.
- **Dead / Respawning:** dashed `rule-strong` border, swatch and name drop to `ink-faint`, feed replaced by `NO SIGNAL · RESPAWN IN 2.4s`, a progress bar and the fragger.

## Sizes
`l` stage (focused) · `m` 8-up wall · `s` flank / strip · `xs` thumbnail (HP bar only, CRIT moves into the nameplate).

## Don't
- Don't put game art, screenshots, logos or emblems in the feed. The placeholder stays a flat rectangle with grid and crosshair.
- Don't use glows, gradients or rounded corners to signal state.
