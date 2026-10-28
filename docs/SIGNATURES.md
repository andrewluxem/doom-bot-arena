# Signature metrics

Every metric named in a `signature` block of `characters/*.yaml`, defined so it can be computed from a bundle's `tics.jsonl` and `events.jsonl`. Two metrics also need item positions from the bundle's `map.json`; they say so. `tools/signatures.py` implements them and reports each bot against its sheet's thresholds.

A sheet's signature is met in a match when every one of its gating metrics passes its `op` and `value`; descriptive metrics are reported and do not count (see Separation). A metric that cannot be computed (no data, such as no frags) is reported as `n/a` and does not pass. `tools/signatures.py` prints each metric of the reported bots against the other seven personalities (milestone 2C) or, for bundles with placeholders, against the placeholders; `--compare` adds the other personalities.

Three metrics use the damage counters schema 3 adds (`hitcount`, `damagecount`, `damage_taken`), so they are `n/a` on schema 1 and 2 bundles: `melee_damage_share`, `chainsaw_damage_share` and `approach_outside_view_cone_share`.

Thresholds changed on 2026-10-28, after the first five-match report:
- VEX: `median_kill_distance < 160` and `closing_share > 0.6` replaced `median_engagement_distance < 320` and `moving_forward_share > 0.6`.
- HEX: `median_first_sight_engagement_distance > 500` replaced `median_engagement_distance > 500`.
- NUL: `frags_within_1500ms_of_visibility_share >= 0.5` replaced `frags_within_1s_of_visibility_share > 0.5`.

Later the same day, after the second evaluation, the separation rule below was added:
- VEX gates on `median_kill_distance`, HEX on `stationary_share`, and NUL on `distance_traveled_rank`.
- `closing_share`, `median_first_sight_engagement_distance` and `frags_within_1500ms_of_visibility_share` became descriptive.
- NUL gained `kills_from_hold_share` as a descriptive candidate.

## Separation

Every signature item carries `gate: true` or `gate: false` in its sheet; an item without `gate` gates.

A metric may gate only if, in the same evaluation:
- **it passes for its personality** in at least 80% of matches (the acceptance bar), and
- **it fails for at least 80% of placeholder bot-matches.** These are every match × every bot whose `controller` is `engine.bots:PlaceholderBot`, measured against the personality's own threshold. A placeholder bot-match where the metric is `n/a` is left out, not counted as failing.

A metric that does not separate is descriptive. `tools/signatures.py` prints both classes and the placeholder pass rate for each metric of the reported bots. It names any gating metric that does not qualify in that evaluation.

Applied to the 2A evaluation (MAP01, seeds 101–105, 25 placeholder bot-matches):

| bot | metric | role | personality passes | placeholders pass |
|---|---|---|---|---|
| VEX | `median_kill_distance < 160` | gate | 5/5 | 4/25 (16%) |
| VEX | `closing_share > 0.6` | descriptive | 5/5 | 19/25 (76%) |
| HEX | `stationary_share > 0.7` | gate | 5/5 | 0/25 |
| HEX | `median_first_sight_engagement_distance > 500` | descriptive | 5/5 | 19/25 (76%) |
| NUL | `distance_traveled_rank <= 2` | gate | 5/5 | 0/25 |
| NUL | `frags_within_1500ms_of_visibility_share >= 0.5` | descriptive | 5/5 | 21/25 (84%) |
| NUL | `kills_from_hold_share >= 0.5` | descriptive | 0/5 | 0/25 |

On 2026-10-28, at the start of milestone 2B, VEX's threshold moved to `median_kill_distance < 140` (data change only). At `< 160` it cleared the rule by one placeholder bot-match.

### Milestone 2B: a placeholder baseline per map

All eight bots are personalities in milestone 2B, so the placeholders come from separate matches. `make docker-baseline` runs five five-minute matches per map with all eight slots as placeholders, at seeds 201 to 205. The baseline bot-matches for a map are every slot of every baseline match on that map: 40 per map.
- **Gating on a map.** A personality's metric gates on a map if it fails for at least 80% of that map's baseline bot-matches, against the personality's own threshold. A baseline bot-match where the metric is `n/a` is left out.
- **Sheet parameters.** Metrics that read the sheet (`engagement_distance_in_band_share` reads `preferred_range`, `retreat_on_close_share` reads `retreat_distance`) are computed for baseline bots with the personality's sheet. In 2A each placeholder's own sheet was used, which made no difference for the 2A metrics.
- **Acceptance.** A bot is accepted if at least one of its metrics gates on every map evaluated and passes in at least 80% of its matches (8 of 10 on MAP10 and E1M5).
- **Rank metrics cannot gate against an all-placeholder baseline.** In every match at least two of the eight bots hold rank 1 or 2 (ties share the better rank), so at least 25% of baseline bot-matches pass a `<= 2`. At most 75% fail, under the 80% the rule needs. This affects `distance_traveled_rank` (NUL's 2A gate) and `armor_pickups_rank` (PYR).
- `tools/signatures.py --baseline <bundles> <bundles>` applies this per map. It prints every metric for all eight bots against the baseline, and the pairs of bots that share a top behavior.

Applied to the 2B evaluation: five matches per map at seeds 301–305, against the baseline at seeds 201–205 (E1M5 seed 206 in place of 202). Sheets carry the resulting `gate` flags.

| bot | metric | E1M5: baseline pass | MAP10: baseline pass | role | met (E1M5 + MAP10) |
|---|---|---|---|---|---|
| VEX | `median_kill_distance < 140` | 0/38 | 1/29 | gate | 3 + 4 = 7/10 |
| VEX | `closing_share > 0.6` | 37/39 | 30/37 | descriptive | 5 + 3 |
| KRN | `item_timing_hit_rate > 0.5` | 0/9 | 1/9 | gate | 0 + 1 = 1/10 (n/a in all five E1M5 matches) |
| KRN | `engagements_started_below_50hp_share < 0.1` | 12/39 | 23/37 | descriptive | 0 + 3 |
| NUL | `frags_within_1500ms_of_visibility_share >= 0.5` | 29/38 | 21/29 | descriptive | 3 + 1 |
| NUL | `distance_traveled_rank <= 2` | 10/40 | 10/40 | descriptive (rank) | 5 + 4 |
| NUL | `kills_from_hold_share >= 0.5` | 0/38 | 0/29 | gate | 3 + 0 = 3/10 |
| ASH | `melee_damage_share == 1.0` | 0/39 | 0/35 | gate | 5 + 5 = 10/10 |
| ASH | `median_engagement_distance < 96` | 0/39 | 0/36 | gate | 0 + 0 = 0/10 |
| ORB | `engagement_distance_in_band_share > 0.6` | 0/39 | 5/37 | gate | 0 + 0 = 0/10 |
| ORB | `retreat_on_close_share > 0.7` | 0/39 | 0/31 | gate | 0 + 0 = 0/10 |
| HEX | `stationary_share > 0.7` | 0/40 | 0/40 | gate | 0 + 4 = 4/10 |
| HEX | `median_first_sight_engagement_distance > 500` | 36/39 | 29/37 | descriptive | 5 + 3 |
| PYR | `approach_outside_view_cone_share > 0.5` | 34/39 | 18/32 | descriptive | 1 + 1 |
| PYR | `armor_pickups_rank <= 2` | 11/40 | 10/40 | descriptive (rank) | 4 + 1 |
| ZED | `behavior_switches_per_minute > 6` | 25/40 | 5/40 | descriptive (MAP10 only) | 5 + 5 |
| ZED | `chainsaw_damage_share > 0.4` | 0/39 | 0/35 | gate | 0 + 0 = 0/10 |

Only ASH meets a gating metric in 8 of 10 matches, and that metric holds by construction: ASH may hold only the fist and chainsaw, and no placeholder ever melees. PYR has no metric that separates on either map. `approach_outside_view_cone_share` is lower for every personality (0.23 to 0.50 by map median) than for placeholders (0.59 to 0.69): placeholders' targets wander and rarely face them, so a placeholder baseline does not suit a metric measured against the target's facing.

### Milestone 2C: the roster baseline

From milestone 2C the placeholder baseline no longer decides gating; `make docker-baseline` stays for frag rates. Separation is against the roster instead:
- **Roster bot-matches.** For a bot on a map, every slot of the other seven personalities in that map's evaluation matches: 35 per map at five matches. Each is measured against the bot's own threshold and, for metrics that read the sheet, with the bot's sheet. One where the metric is `n/a` is left out.
- **Separates on a map** if at least 80% of the roster bot-matches on that map fail it.
- **Gates** if it separates on every map evaluated and the bot passes it in at least 80% of its matches (8 of 10 on MAP10 and E1M5). Acceptance is at least one gating metric per bot.
- Rank metrics can separate now: in a match where the bot holds rank 1 or 2, the other seven fill at least one more rank `<= 2`, so about 1 in 7 roster bot-matches pass.
- `tools/signatures.py` applies this rule whenever every bot in the bundles is a personality (`make signatures-2c`). It prints every metric for all eight bots against the other seven's median and range, and the pairs of bots that share a top behavior.

## Common terms

- **Displayed match.** Tics `frame0_tic … frame0_tic + duration_tics − 1`. Pre-roll tics and events are left out, as they are for the score.
- **Alive tic.** A row with `alive: true`.
- **Life.** A maximal run of consecutive alive tics of one slot. It starts at a respawn (`behavior: RESPAWNING`) or at the first alive tic.
- **Step.** Consecutive tics `t − 1`, `t` of one slot in the same life, both in the displayed match. Its displacement is `(x_t − x_{t−1}, y_t − y_{t−1})`. A step longer than 64 units is a teleport and is left out of every movement metric. Running is about 16 units per tic.
- **Engagement tic.** An alive row whose `target_slot` is not null, while the target's row at the same tic is alive.
  - Its **engagement distance** is the 2D distance between the two rows' `x, y`.
  - `target_slot` starts on the first tic the target is in the bot's view and lasts until 0.6 s after it leaves view (docs/MATCH_FORMAT.md section 5). It is the bot's own report: it names whom the bot engages, not who engages the bot.
- **Engagement.** A maximal run of consecutive tics of one bot with the same non-null `target_slot`. Its start is the run's first tic: two tics after the target's label first appeared, since naming a new label takes two tics (docs/MATCH_FORMAT.md section 2a).
- **Pickup.** An alive step where `health` or `armor` rises, outside the first tic of a life. A rise in `armor` is an armor pickup; a rise in `health` is a health pickup. Ammo is not recorded, so ammo pickups are not seen. Items that do not raise a value at the moment (green armor at 100 or more) are not picked up in Doom either.
- **Rank.** Competition ranking over the eight bots ("1224"): ties share the better rank.
- **Kill.** A credited `frag` event in the displayed match whose `actor` is the bot (any key, barrels included).
- **Damage dealt at tic t.** The rise in the bot's `damagecount` from `t − 1` to `t`: damage to other players and to barrels (docs/MATCH_FORMAT.md section 5). It is attributed to the weapon the bot held at `t − 1` (`weapon`, `weapon_name`), since an attack registers the tic after it is pressed. A rocket's damage lands at impact, so it is attributed to whatever is held then.

## Definitions

### median_engagement_distance
Median engagement distance over all of the bot's engagement tics in the displayed match. Each tic counts once, so long fights weigh more than short ones. `n/a` with fewer than 35 engagement tics.

ASH `< 96` until milestone 2C, when it left ASH's sheet (0 of 10 in 2B).

### median_kill_distance
Median 2D distance between the bot and its victim, both rows at the frag tic, over the bot's kills. `n/a` with no kills. Where a rusher fights, not where it first sees someone.

VEX `< 140`, gating (`< 160` until milestone 2B).

### closing_share
Over the bot's steps whose row at `t` has `behavior: IN_COMBAT` and the same non-null `target_slot` as the row at `t − 1`, with the target alive at both tics: the share where the distance to the target at `t` is smaller than at `t − 1`. Either player's movement counts. `n/a` with no such steps.

VEX `> 0.6`, descriptive: most placeholders pass it too.

### median_first_sight_engagement_distance
Median distance to the target at the start of each of the bot's engagements in the displayed match (target alive at that tic). It measures how far away the bot picks fights up, not how close they end. `n/a` with no engagements.

HEX `> 500`, descriptive: most placeholders pass it too.

### stationary_share
Share of the bot's alive tics in the displayed match at which it is within 32 units of where it was 35 tics earlier, in the same life. The first 35 tics of each life cannot qualify. Small strafes around a held spot count as stationary; knockback that moves the bot more than 32 units in a second does not.

HEX `> 0.7`, gating.

### distance_traveled_rank
Each bot's distance travelled is the sum of its step lengths in the displayed match. Ranked ascending: 1 = least of the eight.

NUL `<= 2`, gating.

### frags_within_1500ms_of_visibility_share
Over the bot's kills, the share where the victim had been in view for at most 1.5 s when it died:
- find the bot's last tic `s ≤` the frag tic with `target_slot` equal to the victim;
- if `s` is more than 35 tics before the frag, the kill does not count (the victim was not the bot's target in the last second);
- otherwise take the start `r` of the engagement containing `s`; the kill counts if `frag_tic − r ≤ 52.5` tics.

Rocket flight time counts. Because an engagement starts two tics after the label appears, the measured time is 0.06 s short of the true time in view. `n/a` with no kills.

NUL `>= 0.5`, descriptive: most placeholders pass it too. The cutoff is 0.5 s longer than NUL's fire window (`fire_only_when_visible_ms: 1000`), which leaves room for rocket flight and a second hit.

### kills_from_hold_share
Over the bot's kills, the share made from a hold: at every tic from `k − 70` to `k − 1`, where `k` is the frag tic, the bot was alive in the same life and within 16 units (2D) of its position at `k`. A kill less than 70 tics into a life does not count, nor does a kill landing after the bot died. `n/a` with no kills.

NUL `>= 0.5`, descriptive. It was added as a second gating candidate for NUL, with the 0.5 set before the metric was computed. It separates from the placeholders (0.000 in all 25 bot-matches) but NUL fails it, at 0.20–0.33. Only 11 of NUL's 44 kills came from a hold; 28 came after it moved more than 100 units in the 2 s before. HEX scores 0.50–1.00.

### engagement_distance_in_band_share
Share of the bot's engagement tics whose engagement distance lies inside its sheet's `temperament.preferred_range` (inclusive).

ORB `> 0.6`, gating.

### retreat_on_close_share
A **close approach** is an engagement tic where the engagement distance is below the sheet's `temperament.retreat_distance`, while the previous tic of the same engagement was not (or the engagement starts inside it).

It is **answered** if, over the next 35 tics of that engagement, the bot's own steps carry it at least 48 units away from the target. Each step is projected on the unit vector from the target to the bot at that step's start, and the projections are summed.

Share of close approaches answered. `n/a` with none.

ORB `> 0.7`, descriptive from milestone 2C.

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

PYR `<= 2` until milestone 2C.

### behavior_switches_per_minute
Count of alive steps where `behavior` differs between `t − 1` and `t`, divided by the bot's alive minutes in the displayed match. A step into or out of `DEAD` or `RESPAWNING` is not a switch.

ZED `> 6`, gating from milestone 2C.

### melee_damage_share
Share of the damage the bot dealt in the displayed match that it dealt while holding a weapon in engine slot 1 (fist or chainsaw) at `t − 1`. Damage to barrels counts; its own splash is not damage dealt. Barrel blasts it set off count, attributed to the weapon in hand. `n/a` with no damage dealt; schema 3 only.

ASH `== 1.0`. ASH may hold only the fist and chainsaw, so a lower value means damage by other means, such as a barrel.

### chainsaw_damage_share
As `melee_damage_share`, counting only damage dealt while engine slot 1 is held and `weapon_name` is `chainsaw`.

ZED `> 0.4`, descriptive from milestone 2C; the chainsaw stays its weapon preference.

### approach_outside_view_cone_share
Over the bot's engagements in the displayed match, take the first tic `t` of each where the bot lands a hit (`hitcount` rises) and its target takes damage (`damage_taken` rises) in the same tic. The engagement counts as outside if, at `t`, the bot lies more than 45 degrees off the target's facing at `t − 1`: outside a 90-degree view cone centred on the target's `angle`. Share over engagements with such a hit. Two attackers hitting the same target in one tic cannot be told apart. `n/a` with none; schema 3 only.

PYR `> 0.5` until milestone 2C.

### third_party_kill_share
Over the bot's kills, the share whose victim was busy with someone else when it died:
- at some tic in the 35 before the frag (`k − 35 … k − 1`), the victim's `target_slot` named a slot other than the bot; or
- at some tic in the 105 before the frag, the victim's `damage_taken` rose while the `damagecount` of a slot other than the bot and the victim rose in the same tic (damage from another slot; two attackers in one tic cannot be told apart).

`n/a` with no kills; schema 3 only.

PYR `>= 0.6`, gating (milestone 2C).

### first_contact_avoidance_share
A **first contact** is another player coming into the bot's view after at least 35 tics out of it, while that player is not busy with anyone else (as in `third_party_kill_share`, at the contact's first tic). "In view" is reconstructed as in `tools/hold_contact.py`: alive, within 2048 units, inside the bot's 90-degree view cone widened by the player's radius, with 2.5D line of sight from the bot's eye.

Share of first contacts the bot does not name as its `target_slot` within the next 35 tics. `n/a` with none; schema 3 only.

PYR `>= 0.5`, descriptive.

## Behavior distribution

Not a signature, but part of milestone 2A's acceptance: a bot's **behavior share** is the fraction of its alive tics in the displayed match spent in each `behavior` value, `RESPAWNING` included. Its top behavior is the one with the largest share.
