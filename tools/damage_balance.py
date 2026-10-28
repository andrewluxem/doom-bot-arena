"""Why a bundle's damage dealt and damage taken differ (schema 3; docs/MATCH_FORMAT.md section 5).

    python3 tools/damage_balance.py bundles/<id>

ViZDoom logs each hit once (VIZ_LogDmg), after armor and not clamped to the health left. The
amount goes to the victim's damage_taken if the victim is a player, and to the source's
damagecount and hitcount if the source is another player's current body. Damage to a thing
that is not a player, such as a barrel, therefore counts as dealt and never as taken. The
report totals:
- armor absorbed: armor lost on tics where a player took damage. Outside both counters, if every
  such tic's health loss equals its damage_taken rise.
- overkill: damage on killing blows beyond the victim's remaining health. In both counters,
  checked on kills where one player dealt and one took damage in that tic.
- dealt with no matching damage taken in the same tic (all players summed), each such tic
  attributed to a barrel: one in the dealer's line of fire, or the blast of a barrel the dealer
  hit BARREL_FUSE tics earlier.
- taken with no matching damage dealt: own splash, the world, or a source that is no longer a
  player's body (a rocket fired before its shooter died).
Dealt minus taken equals the third total minus the fourth.
"""

import argparse
import json
import math
import os
import sys

BARREL_FUSE = 15  # ExplosiveBarrel death: BEXP A 5, B 5, C 5, then D calls A_Explode
LINE_TOLERANCE = 12.0  # degrees: shotgun spread (5.6) plus turning between tics
BARREL_RADIUS = 10.0
PROJECTILE_SPEED = {"rocket_launcher": 20.0, "plasma_rifle": 25.0}  # units per tic


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def load(path):
    with open(os.path.join(path, "match.json")) as f:
        m = json.load(f)
    with open(os.path.join(path, "map.json")) as f:
        mp = json.load(f)
    rows = {}
    for r in read_jsonl(os.path.join(path, "tics.jsonl")):
        rows.setdefault(r["tic"], {})[r["slot"]] = r
    return m, mp, rows


def barrel_in_line(rows, t, s, barrels):
    """(barrel index, distance, bearing offset) of the barrel nearest the dealer's facing, or None.

    The facing is checked at t - 1 and t, and for a projectile over its flight time too."""
    r = rows[t][s]
    speed = PROJECTILE_SPEED.get(rows[t - 1][s]["weapon_name"])
    best = None
    for i, (bx, by) in enumerate(barrels):
        dist = math.hypot(bx - r["x"], by - r["y"])
        back = 1 + (math.ceil(dist / speed) + 1 if speed else 0)
        for k in range(0, back + 1):
            a = rows.get(t - k, {}).get(s)
            if a is None or not a["alive"]:
                break
            d = math.hypot(bx - a["x"], by - a["y"])
            off = wrap180(math.degrees(math.atan2(by - a["y"], bx - a["x"])) - a["angle"])
            if abs(off) <= LINE_TOLERANCE + math.degrees(math.atan2(BARREL_RADIUS, max(d, 1.0))):
                if best is None or abs(off) < abs(best[2]):
                    best = (i, round(d), round(off, 1))
    return best


def balance(path):
    m, mp, rows = load(path)
    slots = [b["slot"] for b in m["bots"]]
    barrels = [(b["x"], b["y"]) for b in mp["barrels"] if b["in_deathmatch"]]
    end = max(rows)
    rise = lambda t, s, k: rows[t][s][k] - rows[t - 1][s][k]  # noqa: E731
    out = {"dealt": sum(rows[end][s]["damagecount"] for s in slots),
           "taken": sum(rows[end][s]["damage_taken"] for s in slots),
           "armor_absorbed": 0, "armor_tics": 0, "hit_tics": 0, "health_loss_equals_taken": 0,
           "overkill": 0, "killing_blows": 0, "single_pair_kills": 0, "killer_dealt_equals_taken": 0,
           "unmatched_dealt": [], "taken_without_dealer": 0}
    hit_by = {s: set() for s in slots}
    for t in range(1, end + 1):
        for s in slots:
            a, b = rows[t - 1][s], rows[t][s]
            taken = rise(t, s, "damage_taken")
            if not a["alive"] or taken <= 0:
                continue
            out["hit_tics"] += 1
            out["health_loss_equals_taken"] += a["health"] - b["health"] == taken
            if a["armor"] > b["armor"]:
                out["armor_absorbed"] += a["armor"] - b["armor"]
                out["armor_tics"] += 1
            if not b["alive"]:
                out["killing_blows"] += 1
                out["overkill"] += taken - a["health"]
                dealers = [k for k in slots if rise(t, k, "damagecount") > 0]
                takers = [k for k in slots if rise(t, k, "damage_taken") > 0]
                if len(dealers) == 1 and takers == [s] and dealers != [s]:
                    out["single_pair_kills"] += 1
                    out["killer_dealt_equals_taken"] += rise(t, dealers[0], "damagecount") == taken
        dealt = sum(rise(t, s, "damagecount") for s in slots)
        taken = sum(rise(t, s, "damage_taken") for s in slots)
        out["taken_without_dealer"] += max(0, taken - dealt)
        if dealt <= taken:
            continue
        for s in slots:
            if rise(t, s, "damagecount") <= 0:
                continue
            hit_by[s].add(t)
            line = barrel_in_line(rows, t, s, barrels)
            blast = t - BARREL_FUSE in hit_by[s]
            out["unmatched_dealt"].append({
                "tic": t, "slot": s, "code": next(b["code"] for b in m["bots"] if b["slot"] == s),
                "weapon": rows[t - 1][s]["weapon_name"], "unmatched": dealt - taken,
                "barrel": "blast" if blast else (f"barrel {line[0]} at {line[1]}, {line[2]:+} deg" if line else None)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle")
    args = ap.parse_args(argv)
    r = balance(args.bundle)
    u = r["unmatched_dealt"]
    tics = sorted({x["tic"] for x in u})
    by_tic = {x["tic"]: x["unmatched"] for x in u}
    total_u = sum(by_tic.values())
    attributed = sum(by_tic[t] for t in tics if all(x["barrel"] for x in u if x["tic"] == t))
    print(f"dealt {r['dealt']}  taken {r['taken']}  dealt - taken {r['dealt'] - r['taken']:+}")
    print(f"armor absorbed {r['armor_absorbed']} over {r['armor_tics']} tics; health loss equals damage_taken "
          f"on {r['health_loss_equals_taken']}/{r['hit_tics']} hit tics, so armor is in neither counter")
    print(f"overkill {r['overkill']} over {r['killing_blows']} killing blows; in damage_taken and in the killer's "
          f"damagecount alike (equal rises on {r['killer_dealt_equals_taken']}/{r['single_pair_kills']} kills "
          f"with one dealer and one victim in the tic)")
    print(f"dealt with no damage taken in the same tic: {total_u} in {len(tics)} tics, "
          f"{attributed} of it attributed to barrels")
    for t in tics:
        who = "; ".join(f"{x['code']} {x['weapon']}: {x['barrel'] or 'no barrel found'}" for x in u if x["tic"] == t)
        print(f"    tic {t}  +{by_tic[t]}  {who}")
    print(f"taken with no damage dealt in the same tic: {r['taken_without_dealer']}")
    print(f"check: {total_u} - {r['taken_without_dealer']} = {total_u - r['taken_without_dealer']:+}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
