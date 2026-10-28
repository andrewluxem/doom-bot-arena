# characters/

One YAML file per bot. The engine loads all eight; a personality is only these dials, never bespoke code.

## Fields

- `slot`, `code`, `name`, `archetype`, `colorset`: identity. `colorset` is passed as `+colorset N`.
- `perception`: `reaction_time_ms` (delay before acting on a newly visible enemy), `aim_accuracy` (0 to 1, lower adds jitter), `max_turn_deg_per_tic` (cap on delta turn).
- `temperament`: `aggression` and `self_preservation` are 0 to 1 multipliers on engage/pursue and flee/heal utilities. `engage_health_min` blocks starting fights below that HP unless cornered. `preferred_range` is [min, max] in map units (Doom units, 64 = one grid square).
- `behavior_weights`: multipliers on the utility score of each behavior. 0 disables it. The label shown to viewers is the behavior name.
- `weapons.allowed`: the only weapons the bot may select or fire. `weapons.weights`: preference when several are held.
- `items.weights`: pickup utility by item class. `item_timing: true` means the bot tracks respawn timers and routes to items as they reappear.
- `spots`: for stationary styles. `mode` is `sightlines_max` (perch) or `sightlines_min_near_traffic` (ambush). `leave_when` lists conditions that break the hold.
- `signature`: the metrics that prove the personality, measured from tics.jsonl. Acceptance is meeting them in 80% of matches.

## Weapon names to ViZDoom slots (Doom II / Freedoom Phase 2)

| name | slot button | notes |
|---|---|---|
| fist | SELECT_WEAPON1 | melee |
| chainsaw | SELECT_WEAPON1 | melee, shares slot 1 |
| pistol | SELECT_WEAPON2 | hitscan |
| shotgun | SELECT_WEAPON3 | |
| super_shotgun | SELECT_WEAPON3 | shares slot 3 |
| chaingun | SELECT_WEAPON4 | hitscan, long range |
| rocket_launcher | SELECT_WEAPON5 | splash damages self |
| plasma_rifle | SELECT_WEAPON6 | |
| bfg | SELECT_WEAPON7 | |

Slots 1 and 3 hold two weapons; pressing the slot again toggles. The engine resolves `allowed` to the right press sequence.

## Behavior vocabulary (the only values allowed in tics.jsonl `behavior`)

IN_COMBAT, HUNTING, STALKING, HOLDING_ANGLE, ROAMING, COLLECTING_ARMOR, COLLECTING_HEALTH, FLEEING, DEAD, RESPAWNING
