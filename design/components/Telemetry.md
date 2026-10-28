# Telemetry

The focused bot's live panel: personality, behaviour label, HP and armour, weapon, frags/deaths, zone, target, and a one-click `Return to Multiview [ESC]`.

Render with `DBA.telemetry(id, {compact})`. Full in Option A's right column; compact (4-column grid, return action in the header) under Option B's stage.

- A 4px bot-colour top rule ties the panel to the feed.
- The behaviour label is the one filled element (`on-bot` text on the bot colour); everything else stays ink on surface.
