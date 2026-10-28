# Option C · Desktop

Swiss Editorial Telemetry: a warm paper page (`surface-0` #F2F0EA in the Paper theme) set like a printed results sheet: stark ink rules, a full-height standings column, a 2×4 grid where each feed is led by a bold grotesque numeral block, and a right rail for clock, timeline, kill feed and the pinned map.

**Core idea.** Typography does the ranking and state work: numerals, rules and whitespace make the page legible at a glance and printable or screenshot-friendly for recaps. **Trade-off.** Feeds are the smallest of the three (≈232px) and a light ground is harsh in a dark room, so it suits analysts and second-screen viewers more than a lean-back watch party.

## Layout
- Columns: standings 300 · grid 784 · rail (≈252). 24px gutters and frame (`space-6`).
- Each tile: 148px label block (numeral `ed-numeral`, frags, name/role, state, HP, weapon) + 4:3 feed with a 1px ink frame.
- In Combat: the numeral block fills with the bot colour, state flips to an ink chip, feed frame goes to 3px bot colour. Dead: dashed rules, faint numerals, countdown in the feed.
- The map is pinned to the bottom of the rail (bottom-right), outside the grid.
