"""Item facts shared by the personality engine, spot scoring and the signature metrics."""

TICRATE = 35
RESPAWN_TICS = 30 * TICRATE  # ZDoom deathmatch with sv_itemrespawn: an item reappears 30 s after its pickup
# Items worth timing (docs/SIGNATURES.md, item_timing_hit_rate), by the value a pickup raises.
TIMED = {"Stimpack": "health", "Medikit": "health", "Soulsphere": "health",
         "GreenArmor": "armor", "BlueArmor": "armor", "Megasphere": "armor"}
