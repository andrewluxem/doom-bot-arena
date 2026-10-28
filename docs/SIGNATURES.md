# Signature metrics

Every metric named in a `signature` block of `characters/*.yaml`, defined so it can be computed from a bundle's `tics.jsonl` and `events.jsonl`. Two metrics also need item positions from the bundle's `map.json`; they say so. `tools/signatures.py` implements them and reports each bot against its sheet's thresholds.

A sheet's signature is met in a match when every one of its metrics passes its `op` and `value`. A metric that cannot be computed (no data, such as no frags) is reported as `n/a` and does not pass.

Three metrics cannot be computed as named, because schema 2 records no damage and no shots. Each is defined below by the closest quantity the bundle does record, and marked **proxy**:
- `melee_damage_share`
- `chainsaw_damage_share`
- `approach_outside_view_cone_share`

## Common terms

- **Displayed match.** Tics `frame0_tic … frame0_tic + duration_tics − 1`. Pre-roll tics and events are left out, as they are for the score.
- **Alive tic.** A row with `alive: true`.
- **Life.** A maximal run of consecutive alive tics of one slot. It starts at a respawn (`behavior: RESPAWNING`) or at the first alive tic.
- **Step.** Consecutive tics `t − 1`, `t` of one slot in the same life, both in the displayed match. Its displacement is `(x_t − x_{t−1}, y_t − y_{t−1})`. A step longer than 64 units is a teleport and is left out of every movement metric. Running is about 16 units per tic.
- **Engagement tic.** An alive row whose `target_slot` is not null, while the target's row at the same tic is alive.
  - Its **engagement distance** is the 2D distance between the two rows' `x, y`.
  - `target_slot` starts on the first tic the target is in the bot's view and lasts until 0.6 s after it leaves view (docs/MATCH_FORMAT.md section 5). It is the bot's own report: it names whom the bot engages, not who engages the bot.
- **Engagement.** A maximal run of consecutive tics of one bot with the same non-null `target_slot`. Its start is the run's first tic.
- **Pickup.** An alive step where `health` or `armor` rises, outside the first tic of a life. A rise in `armor` is an armor pickup; a rise in `health` is a health pickup. Ammo is not recorded, so ammo pickups are not seen. Items that do not raise a value at the moment (green armor at 100 or more) are not picked up in Doom either.
- **Rank.** Competition ranking over the eight bots ("1224"): ties share the better rank.

## Definitions

### median_engagement_distance
Median engagement distance over all of the bot's engagement tics in the displayed match. Each tic counts once, so long fights weigh more than short ones. `n/a` with fewer than 35 engagement tics.

Used by VEX (`< 320`), ASH (`< 96`) and HEX (`> 500`).

### moving_forward_share
Share of the bot's steps whose displacement, projected on its facing at `t − 1` (`angle`, 0 = east, counter-clockwise), exceeds 1 unit. Standing still, strafing and backing up all count against it.

VEX `> 0.6`.

### stationary_share
Share of the bot's alive tics in the displayed match at which it is within 32 units of where it was 35 tics earlier, in the same life. The first 35 tics of each life cannot qualify. Small strafes around a held spot count as stationary; knockback that moves the bot more than 32 units in a second does not.

HEX `> 0.7`.

### distance_traveled_rank
Each bot's distance travelled is the sum of its step lengths in the displayed match. Ranked ascending: 1 = least of the eight.

NUL `<= 2`.

### frags_within_1s_of_visibility_share
Over the bot's credited `frag` events in the displayed match (any key, barrels included), the share where the victim had been in view for at most 1 s when it died:
- find the bot's last tic `s ≤` the frag tic with `target_slot` equal to the victim;
- if `s` is more than 35 tics before the frag, the frag does not count as within 1 s (the victim was not the bot's target in the last second);
- otherwise take the start `r` of the engagement containing `s`; the frag counts if `frag_tic − r ≤ 35`.

Rocket flight time counts: a rocket fired 0.9 s after the target appeared and landing 0.5 s later is a frag at 1.4 s. `n/a` with no frags.

NUL `> 0.5`.

### engagement_distance_in_band_share
Share of the bot's engagement tics whose engagement distance lies inside its sheet's `temperament.preferred_range` (inclusive).

ORB `> 0.6`.

### retreat_on_close_share
A **close approach** is an engagement tic where the engagement distance is below the sheet's `temperament.retreat_distance`, while the previous tic of the same engagement was not (or the engagement starts inside it).

It is **answered** if, over the next 35 tics of that engagement, the bot's own steps carry it at least 48 units away from the target. Each step is projected on the unit vector from the target to the bot at that step's start, and the projections are summed.

Share of close approaches answered. `n/a` with none.

ORB `> 0.7`.

### engagements_started_below_50hp_share
Share of the bot's engagements in the displayed match whose start tic has `health < 50`. `target_slot` is the bot's own report, so this measures fights the bot took up while hurt, not ones it was dragged into.

KRN `< 0.1`.

### item_timing_hit_rate
Needs `map.json` item positions, and counts only items worth timing: `Stimpack`, `Medikit`, `Soulsphere`, `GreenArmor`, `BlueArmor`, `Megasphere`.
- Every pickup, by any bot, is attributed to the nearest such item of its kind (health or armor) within 64 units. Pickups with none in range are dropped.
- An item taken at tic `p` respawns at `p + 1050` (ZDoom deathmatch with `sv_itemrespawn`: 30 s).
- A bot's pickup of an item whose previous pickup in this match is known is **timed** if it falls in `[respawn, respawn + 70]` (within 2 s of the respawn).

Share of such pickups that are timed. `n/a` with none. Pickups by players the item was not visible to still set the clock, which is what a player tracking the timer would know.

KRN `> 0.5`.

### armor_pickups_rank
Each bot's armor pickups in the displayed match, ranked descending: 1 = most of the eight.

PYR `<= 2`.

### behavior_switches_per_minute
Count of alive steps where `behavior` differs between `t − 1` and `t`, divided by the bot's alive minutes in the displayed match. A step into or out of `DEAD` or `RESPAWNING` is not a switch.

ZED `> 6`.

### melee_damage_share (proxy)
Schema 2 records no damage. Defined instead as the share of the bot's credited `frag` events in the displayed match whose key is `OB_MPFIST` or `OB_MPCHAINSAW`. `n/a` with no frags.

ASH `== 1.0`. ASH may hold only the fist and chainsaw, so a lower value means frags by other means: barrels and telefrags. Those are frags, not damage, which this proxy cannot separate.

### chainsaw_damage_share (proxy)
As `melee_damage_share`, counting only `OB_MPCHAINSAW`.

ZED `> 0.4`.

### approach_outside_view_cone_share (proxy)
Schema 2 records no shots, so the "first shot" is replaced by the engagement start. Over the bot's engagements in the displayed match, it is the share where, at the start tic, the bot lies more than 45 degrees off the target's facing: outside a 90-degree view cone centred on the target's `angle`. `n/a` with none.

PYR `> 0.5`.

## Behavior distribution

Not a signature, but part of milestone 2A's acceptance: a bot's **behavior share** is the fraction of its alive tics in the displayed match spent in each `behavior` value, `RESPAWNING` included. Its top behavior is the one with the largest share.
