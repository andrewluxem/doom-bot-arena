# Maps for milestone 2B

`tools/survey_maps.py` over Freedoom 0.13 (the IWADs shipped with ViZDoom 1.3.2): freedoom2 MAP01 to MAP10 and freedoom1 E1M1 to E1M9. No matches were run on these maps.

A pickup's reach is the number of deathmatch starts from which a player can get to it without riding a lift, and with one where that differs. Reach comes from the personality engine's nav graph and Doom's pickup rule (boxes touch, item floor from 8 below to 56 above the player's). It counts walking, doors, teleporters, and remotely opened doors, which are treated as openable. Key-locked doors are walls, because keys do not spawn in deathmatch. Switch-raised stairs, crushers and damaging floors are not modelled. So a 0 means "not without one of those", not "impossible".

| IWAD | map | DM starts | weapons | chainsaw: starts reaching it without a lift | berserk | armor items | timed items | lifts |
|---|---|---|---|---|---|---|---|---|
| freedoom2 | MAP01 | 10 | CG saw plasma RL SG SSG | 1/10 (10 with lift) | none | 22 | 14 | 1 |
| freedoom2 | MAP02 | 6 | BFG CG plasma RL SG SSG | none | none | 25 | 8 | 2 |
| freedoom2 | MAP03 | 4 | CG saw plasma RL SG SSG | 0/4, 0/4 | none | 18 | 19 | 4 |
| freedoom2 | MAP04 | 8 | CG plasma RL SG SSG | none | none | 45 | 15 | 2 |
| freedoom2 | MAP05 | 10 | CG plasma RL SG SSG | none | none | 4 | 46 | 2 |
| freedoom2 | MAP06 | 6 | BFG CG plasma RL SG SSG | none | none | 11 | 10 | 1 |
| freedoom2 | MAP07 | 7 | CG saw plasma RL SG SSG | 0/7 | 2/7 (6 with lift) | 14 | 19 | 8 |
| freedoom2 | MAP08 | 9 | CG saw plasma RL SG SSG | 0/9 | 0/9 | 16 | 21 | 1 |
| freedoom2 | MAP09 | 10 | CG saw plasma RL SG SSG | 5/10 | 0/10 | 23 | 30 | 2 |
| freedoom2 | MAP10 | 10 | CG saw plasma RL SG SSG | 7/10 | none | 17 | 30 | 2 |
| freedoom1 | E1M1 | 8 | CG saw plasma RL SG | 0/8 | 4/8 (8 with lift) | 19 | 8 | 2 |
| freedoom1 | E1M2 | 9 | BFG CG saw plasma RL SG | 0/9 | none | 36 | 20 | 5 |
| freedoom1 | E1M3 | 5 | CG saw RL SG | 0/5 | none | 46 | 32 | 17 |
| freedoom1 | E1M4 | 7 | CG saw RL SG | 3/7 (4 with lift) | none | 43 | 18 | 2 |
| freedoom1 | E1M5 | 9 | CG saw plasma RL SG | 7/9 (8 with lift), 7/9 (8 with lift) | none | 47 | 10 | 2 |
| freedoom1 | E1M6 | 6 | CG saw plasma RL SG | 0/6 | 4/6 | 35 | 30 | 5 |
| freedoom1 | E1M7 | 10 | CG saw RL SG | 0/10 | none | 60 | 40 | 18 |
| freedoom1 | E1M8 | 5 | CG plasma RL SG | none | none | 5 | 23 | 0 |
| freedoom1 | E1M9 | 5 | CG saw plasma RL SG | 0/5, 0/5 (3 with lift) | none | 23 | 26 | 8 |

Weapons: CG chaingun, saw chainsaw, SG shotgun, SSG super shotgun, RL rocket launcher. Timed items are stimpacks, medikits, soulspheres, green and blue armor, and megaspheres.

## Decision

**freedoom2 MAP10 and freedoom1 E1M5**, recommended below and confirmed for 2B on 2026-10-28. **E1M1 is a candidate third map** for ASH's berserk (see the end of this section).
- **Spawns.** Both have at least 9 deathmatch starts for 8 players (MAP01 has 10).
- **Chainsaw.** Both have a chainsaw reachable without a lift from 7 starts, which ASH (fist and chainsaw only) and ZED's `chainsaw_damage_share` need. MAP01's only chainsaw is in a lift-only pocket.
- **Weapons.** Both have the plasma rifle ORB prefers, the rocket launcher KRN and NUL prefer, and HEX's chaingun.
- **How they differ.**
  - MAP10 has every Doom II weapon including the super shotgun, 30 timed items (KRN's `item_timing_hit_rate`) and a megasphere.
  - E1M5 is a Doom 1 map with no super shotgun, so VEX and PYR resolve to the shotgun. Its 47 armor items suit PYR's `armor_pickups_rank`.

**What neither has:** a berserk pack reachable without a lift. E1M1 (8 starts, berserk reachable from 4) is the candidate third map for ASH's berserk, alongside MAP10 and E1M5 rather than replacing either: its chainsaw is reachable from none. No matches have been run on any of the three.

## Found in milestone 2B: regions and pockets

On the static map (map.json heights, so floors that W1/S1 lines lower during play are not modelled), both maps split into separate navigation regions. The survey's reach column already showed this ("7/10"), but it was not called out. Nav graph version 6 (`engine/personality/nav.py`) keeps every region of 64 nodes or more. One-way edges are kept only toward a larger region.

| map | largest region | other regions with deathmatch starts | how the others connect in play |
|---|---|---|---|
| MAP10 | 5,887 nodes, 6 starts | 1,634 nodes, 2 starts, plus a 371-node region that drops into it, 1 start | W1/S1 floor-lowering lines and key doors (keys never spawn in deathmatch) |
| MAP10 | | a 164-node pocket at (−2080, −1056), 1 start | entered by WR teleporter lines tagged 12; left only by an S1 floor switch (line 2918) |
| E1M5 | 4,643 nodes, 8 starts | the 291-node exit room at (−1188, 1088), 1 start, with a rocket launcher 20 units from the start | left only by a red key door, or by the exit switch, which kills the player under `sv_noexit` (`OB_EXIT`) |

- **Placeholders.** In the MAP10 baseline (seed 201), three of the eight placeholders spent most of the match in the 1,634-node region. They crossed between regions only by respawning. Placeholders that spawn in E1M5's exit room stay there until they die, usually on the exit switch, which they press at random.
- **Personalities** keep to the region they spawn in. A bot spawned into a pocket (fewer than 600 nodes reachable, every door open) uses the pocket's exit switch once no enemy is in view, or the console `kill` where there is none (MAP10's pocket). It then respawns elsewhere. Both count as suicides.
- **Level-start spawns are random** (MATCH_FORMAT section 2), so a bot starts in a pocket in roughly half the matches on each map.

**Placeholder frag rate** (`make docker-baseline`, five five-minute matches per map, all eight slots placeholders, seeds 201–205; E1M5 seed 206 replaced 202, whose capture failed twice on the host player-name fault):

| map | frags per minute, all eight bots | range over matches | suicides in 5 matches |
|---|---|---|---|
| MAP01 | 11.92 | 11.00–13.40 | 14 |
| MAP10 | 2.12 | 1.00–2.80 | 4 |
| E1M5 | 6.88 | 6.40–7.60 | 14 |

MAP10's rate is a sixth of MAP01's: the map is large and split, and placeholders roaming at random rarely meet. Kill-based metrics on MAP10 rest on few kills per bot.
