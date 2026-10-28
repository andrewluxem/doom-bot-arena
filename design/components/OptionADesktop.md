# Option A · Desktop

Broadcast Multiviewer: a dense, zero-waste 4×2 wall of 4:3 feeds that fills the viewport like an OB-van production monitor, with standings as a tight data strip on top and the timeline, kill feed and pinned map docked in the aspect-ratio margin under the wall.

**Core idea.** Every feed gets equal real estate and the chrome stays out of the picture, so the eye is pulled only by tile state (the bot-coloured frame and filled nameplate on 01 and 02, the dashed, desaturated respawn tile on 04). **Trade-off.** It is the most faithful to how directors watch, but the map is small and spatial context (who is near whom) has to be read from the map rather than from the layout.

## Layout
- 8px frame inset, 4px (`space-2`) gutters. 4:3 is enforced by `aspect-ratio` on `.feed`; tiles never crop.
- The 4×2 wall's height is fixed by the 4:3 ratio; the remaining bottom band (≈190px at 1440×900) is the dock: timeline (flex), kill feed (340px), pinned map (226px, bottom-right).
- The map lives in its own dock cell, so it can never overlap a nameplate or HP bar.

## Interaction
- Click / Enter on a tile → Focused state (`OptionAFocused`). Keys 1–8 jump to a bot.
- Click the map or press M → Map expanded (`OptionAMapExpanded`).
