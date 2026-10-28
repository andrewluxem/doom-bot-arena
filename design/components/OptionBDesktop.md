# Option B · Desktop

Spatial Arena Ring: the top-down wireframe map is the central anchor and the eight 4:3 feeds sit on its west and east flanks in the order their bots occupy the arena north to south, each tethered to its dot by a hairline callout.

**Core idea.** Spatial context is free: you see that `03 NUL` is one door away from the `01`/`02` duel before anything happens, and each feed sits next to the part of the map it shows. **Trade-off.** Feeds are smaller (216px) and they reorder as bots move, so it suits viewers following the story of the match more than those studying individual POVs.

## Layout
- West flank: 07 PYR (NW), 03 NUL (N), 01 VEX (Hall W), 04 ASH (W door). East flank: 05 ORB (NE), 02 KRN (Hall E), 06 HEX (SE), 08 ZED (S). Sector chips on each nameplate.
- Tethers: 1px bot-colour lines at 55% opacity from the feed's inner edge to the dot, with an 18px horizontal lead so they leave tiles cleanly; dashed grey for the dead bot's `×`.
- Below the map: standings table with state column, timeline and the last frags.
- Re-sort is debounced (a tile moves flanks only after its bot holds the new side for 3s) to avoid churn.
- Focusing a bot swaps its feed into the centre and pins the map bottom-right (`OptionBFocused`).
