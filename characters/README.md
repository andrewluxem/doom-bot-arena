# characters/

One YAML file per bot. The engine loads all eight; a personality is only these dials, never bespoke code.

## Fields

- `slot`, `code`, `name`, `archetype`, `colorset`: identity. `colorset` is passed as `+colorset N`.
- `perception`: `reaction_time_ms` (delay before acting on a newly visible enemy), `aim_accuracy` (0 to 1, lower adds jitter), `max_turn_deg_per_tic` (cap on delta turn).
- `temperament`: `aggression` and `self_preservation` are 0 to 1 multipliers on engage/pursue and flee/heal utilities. `engage_health_min` blocks starting fights below that HP unless cornered. `preferred_range` is [min, max] in map units (Doom units, 64 = one grid square).
  - `retreat_distance`: map units, or `null`. Engaging a target closer than this, the bot backs straight off on the nearest walkable heading (within 90 degrees of directly away) while it keeps aiming and firing: the retreat band. It is a hard rule, not a utility term: it overrides the move of every behavior while the bot engages (milestone 2C). While the target is in view and in reach of the weapon held, the label is `KITING`.
  - `engage_only`: `null`, or which targets the bot may engage (PYR, milestone 2C). It engages only an enemy it has seen aiming at another player in the last second (`busy: true`) or bleeding within `hurt_within_ms` (a `Blood` label at the enemy). Any other enemy closer than 1024 units it keeps away from, on the nearest walkable heading away, unless one is within 128 units: that one it fights. It hunts only enemies it has seen busy or hurt.
  - `flank`: `true` or `false`. Paths toward a known enemy cost four times as much inside that enemy's 90-degree view cone (its facing when last seen, within 1024 units). The approach goal moves 160 units behind the enemy, and in a fight the bot strafes out of the cone the short way.
- `behavior_weights`: multipliers on the utility score of each behavior. 0 disables it. The label shown to viewers is the behavior name.
- `weapons.allowed`: the only weapons the bot may select or fire. `weapons.weights`: preference when several are held. A bot holding no usable weapon rated at least half its best goes to fetch one. With no enemy to look for, it also fetches any allowed weapon rated above everything it holds (ASH's chainsaw, ORB's plasma rifle). Melee weapons (fist, chainsaw) fire only within reach.
- `items.weights`: pickup utility by item class.
  - `berserk` at 0.5 or more draws the bot to a berserk pack while it has none. A berserk triples the fist's weapon fitness until the bot dies, since berserk multiplies fist damage by 10.
  - `item_timing: true` means the bot tracks respawn timers and routes to items as they reappear.
    - It times the items it picks up itself.
    - It also times items it sees vanish: a place in its view and line of sight where the item's label is missing.
    - Item goals count items due back by the time it arrives. Roaming, it heads for the timed item it can reach nearest its respawn, and it waits on the spot until the item reappears.
- `reroll_interval_s`, `reroll_seed_from_match`, `ranges`: dial re-rolls, or `null`, `false`, `null`.
  - Every `reroll_interval_s` of match time, each dial under `ranges` (a dotted path such as `temperament.aggression`, with `[low, high]`) is drawn again, uniformly.
  - With `reroll_seed_from_match: true` the draw is seeded from the match seed, the slot and the interval's index, so a replay draws the same values at the same times.
  - The sheet's own values hold until the first roll.
- `spots`: for stationary styles. `mode` is `sightlines_max` (perch) or `sightlines_min_near_traffic` (ambush). `leave_when` lists conditions that break the hold.
- `signature`: the metrics that prove the personality, measured from tics.jsonl. Each item is `metric`, `op`, `value` and `gate`. Acceptance is meeting the gating items (`gate: true`, the default) in 80% of matches. An item may gate only if it separates the personality from the other seven personalities in the same evaluation (docs/SIGNATURES.md, separation; the placeholders until milestone 2C). Otherwise it is `gate: false`: reported, but not part of acceptance.

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

IN_COMBAT, HUNTING, STALKING, HOLDING_ANGLE, ROAMING, COLLECTING_ARMOR, COLLECTING_HEALTH, FLEEING, KITING, DEAD, RESPAWNING

`KITING` is never chosen by the utility scores: it is the label of the retreat band (`retreat_distance`) while the target is in view and in weapon range.
