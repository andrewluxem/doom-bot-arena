# Minimap

A top-down wireframe of the arena (orthogonal and 45° walls only) with a dot, 2-digit ID and field-of-view cone for each living bot, and an `×` where a bot died.

Render with `DBA.mapPanel(opts)`; `opts.ls` label size, `r` dot radius, `cone` cone length (map units), `callsigns`, `sectors`, `ids:false` for phone-size, `expanded`, `tap`.

- Always pinned bottom-right in 8-up and focused views, in its own layout cell (never floating over feeds).
- Expands from that corner on click / tap / `M`; collapses on `M` or Esc.
- Cones: 60° wedge, bot colour at 20% fill / 75% stroke. In the snapshot `01` and `02` face each other in the Central Hall.
- Walls in `ink-muted` at 1.5px non-scaling stroke; grid in `rule-soft`; sector labels in `ink-faint`.
