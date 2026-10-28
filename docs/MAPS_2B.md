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

## Recommendation

**freedoom2 MAP10 and freedoom1 E1M5.**
- **Spawns.** Both have at least 9 deathmatch starts for 8 players (MAP01 has 10).
- **Chainsaw.** Both have a chainsaw reachable without a lift from 7 starts, which ASH (fist and chainsaw only) and ZED's `chainsaw_damage_share` need. MAP01's only chainsaw is in a lift-only pocket.
- **Weapons.** Both have the plasma rifle ORB prefers, the rocket launcher KRN and NUL prefer, and HEX's chaingun.
- **How they differ.**
  - MAP10 has every Doom II weapon including the super shotgun, 30 timed items (KRN's `item_timing_hit_rate`) and a megasphere.
  - E1M5 is a Doom 1 map with no super shotgun, so VEX and PYR resolve to the shotgun. Its 47 armor items suit PYR's `armor_pickups_rank`.

**What neither has:** a berserk pack reachable without a lift. If ASH's berserk behaviour matters for 2B, E1M1 (8 starts, berserk from 4) is the alternative to E1M5, but its chainsaw is reachable from none.
