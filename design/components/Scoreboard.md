# Scoreboard

An 8-row leaderboard sorted by frags: `Swatch + ID`, `Name`, `F`, `D`, all numerals in `tabular-nums` so columns never jitter as scores change.

Variants: `DBA.scoreStrip()` (A, horizontal top strip), `DBA.scoreTable({state})` (B and phone drawers), `DBA.scoreColumn()` (C, full-height).

- In Combat rows get a 3px bot-colour bar (bottom edge in the strip, left edge in tables); the dead row drops to `ink-faint`.
- Ties keep the previous order (stable sort) so rows don't flicker.
