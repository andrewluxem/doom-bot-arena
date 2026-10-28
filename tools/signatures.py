"""Signature metrics per bot, from match bundles (docs/SIGNATURES.md).

    python3 tools/signatures.py bundles/<id> [bundles/<id> ...] [--bots VEX,HEX,NUL] [--json out.json]

Every metric is computed for all eight bots, so ranks are defined. Each bot is then
checked against the thresholds in its own characters/*.yaml signature block. With
several bundles, a summary counts the matches in which each bot met its whole signature.
"""

import argparse
import json
import math
import operator
import os
import sys
from collections import Counter

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster  # noqa: E402

TICRATE = 35
TELEPORT_STEP = 64.0
WINDOW = 35
STATIONARY_RADIUS = 32.0
FORWARD_MIN = 1.0
VIEW_CONE_HALF = 45.0
RETREAT_MIN = 48.0
ITEM_RESPAWN = 1050
TIMED_SLACK = 70
TIMED_ITEMS = {"Stimpack": "health", "Medikit": "health", "Soulsphere": "health",
               "GreenArmor": "armor", "BlueArmor": "armor", "Megasphere": "armor"}
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class Bundle:
    def __init__(self, path):
        self.path = path
        with open(os.path.join(path, "match.json")) as f:
            self.m = json.load(f)
        self.f0 = self.m["frame0_tic"]
        self.end = self.f0 + self.m["duration_tics"]  # exclusive
        self.slots = [b["slot"] for b in self.m["bots"]]
        self.code = {b["slot"]: b["code"] for b in self.m["bots"]}
        self.rows = {s: [None] * self.end for s in self.slots}
        for r in read_jsonl(os.path.join(path, "tics.jsonl")):
            self.rows[r["slot"]][r["tic"]] = r
        self.events = read_jsonl(os.path.join(path, "events.jsonl"))
        self._map = None
        self.life = {s: self._lives(s) for s in self.slots}

    @property
    def map(self):
        if self._map is None:
            with open(os.path.join(self.path, "map.json")) as f:
                self._map = json.load(f)
        return self._map

    def _lives(self, s):
        life, n, prev = [None] * self.end, 0, False
        for t, r in enumerate(self.rows[s]):
            if r["alive"]:
                if not prev:
                    n += 1
                life[t] = n
            prev = r["alive"]
        return life

    def displayed(self):
        return range(self.f0, self.end)

    def steps(self, s):
        """(t, r_prev, r) for consecutive tics in one life inside the displayed match, teleports dropped."""
        rows, life = self.rows[s], self.life[s]
        for t in range(self.f0 + 1, self.end):
            if life[t] is None or life[t] != life[t - 1]:
                continue
            a, b = rows[t - 1], rows[t]
            if math.hypot(b["x"] - a["x"], b["y"] - a["y"]) <= TELEPORT_STEP:
                yield t, a, b

    def engagement_tics(self, s):
        out = []
        for t in self.displayed():
            r = self.rows[s][t]
            tg = r["target_slot"] if r["alive"] else None
            if tg is not None and self.rows[tg][t]["alive"]:
                o = self.rows[tg][t]
                out.append((t, tg, math.hypot(o["x"] - r["x"], o["y"] - r["y"])))
        return out

    def engagements(self, s):
        """[(start, end, target)] runs of one non-null target_slot, started in the displayed match."""
        runs, cur = [], None
        for t in range(self.end):
            tg = self.rows[s][t]["target_slot"]
            if cur and tg == cur[2] and t == cur[1] + 1:
                cur[1] = t
                continue
            if cur:
                runs.append(tuple(cur))
            cur = [t, t, tg] if tg is not None else None
        if cur:
            runs.append(tuple(cur))
        return [r for r in runs if r[0] >= self.f0]

    def frags_by(self, s):
        return [e for e in self.events if e["type"] == "frag" and e["actor"] == s and e["credited"]
                and e["tic"] >= self.f0]

    def pickups(self, s):
        """[(tic, kind)] health and armor rises inside one life."""
        out = []
        for t, a, b in self.steps(s):
            if b["armor"] > a["armor"]:
                out.append((t, "armor"))
            if b["health"] > a["health"]:
                out.append((t, "health"))
        return out


# ---------------------------------------------------------------------- metrics
def median_engagement_distance(B, s, sheet):
    d = sorted(x[2] for x in B.engagement_tics(s))
    if len(d) < WINDOW:
        return None
    n = len(d)
    return d[n // 2] if n % 2 else (d[n // 2 - 1] + d[n // 2]) / 2


def moving_forward_share(B, s, sheet):
    n = fwd = 0
    for t, a, b in B.steps(s):
        n += 1
        ang = math.radians(a["angle"])
        if (b["x"] - a["x"]) * math.cos(ang) + (b["y"] - a["y"]) * math.sin(ang) > FORWARD_MIN:
            fwd += 1
    return fwd / n if n else None


def stationary_share(B, s, sheet):
    rows, life = B.rows[s], B.life[s]
    alive = still = 0
    for t in B.displayed():
        if life[t] is None:
            continue
        alive += 1
        if t >= WINDOW and life[t - WINDOW] == life[t]:
            a, b = rows[t - WINDOW], rows[t]
            if math.hypot(b["x"] - a["x"], b["y"] - a["y"]) < STATIONARY_RADIUS:
                still += 1
    return still / alive if alive else None


def distance_traveled(B, s):
    return sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for _, a, b in B.steps(s))


def distance_traveled_rank(B, s, sheet):
    d = {k: distance_traveled(B, k) for k in B.slots}
    return 1 + sum(1 for k in B.slots if d[k] < d[s])


def frags_within_1s_of_visibility_share(B, s, sheet):
    frags = B.frags_by(s)
    if not frags:
        return None
    rows, ok = B.rows[s], 0
    for e in frags:
        v, k = e["target"], e["tic"]
        last = next((t for t in range(k, max(-1, k - WINDOW - 1), -1) if rows[t]["target_slot"] == v), None)
        if last is None:
            continue
        r = last
        while r > 0 and rows[r - 1]["target_slot"] == v:
            r -= 1
        if k - r <= WINDOW:
            ok += 1
    return ok / len(frags)


def engagement_distance_in_band_share(B, s, sheet):
    lo, hi = sheet["temperament"]["preferred_range"]
    d = [x[2] for x in B.engagement_tics(s)]
    return sum(1 for x in d if lo <= x <= hi) / len(d) if d else None


def retreat_on_close_share(B, s, sheet):
    close = sheet["temperament"].get("retreat_distance")
    if close is None:
        return None
    rows, approaches, answered = B.rows[s], 0, 0
    for start, end, tg in B.engagements(s):
        def dist(t):
            a, b = rows[t], B.rows[tg][t]
            return math.hypot(b["x"] - a["x"], b["y"] - a["y"]) if b["alive"] and a["alive"] else None
        for t in range(start, end + 1):
            d = dist(t)
            if d is None or d >= close:
                continue
            prev = dist(t - 1) if t > start else None
            if t > start and (prev is None or prev < close):
                continue
            approaches += 1
            away = 0.0
            for u in range(t + 1, min(end, t + WINDOW) + 1):
                a, b, o = rows[u - 1], rows[u], B.rows[tg][u - 1]
                if not (a["alive"] and b["alive"]):
                    break
                dx, dy = a["x"] - o["x"], a["y"] - o["y"]
                n = math.hypot(dx, dy) or 1.0
                away += ((b["x"] - a["x"]) * dx + (b["y"] - a["y"]) * dy) / n
            if away >= RETREAT_MIN:
                answered += 1
    return answered / approaches if approaches else None


def engagements_started_below_50hp_share(B, s, sheet):
    eng = B.engagements(s)
    return sum(1 for start, _, _ in eng if B.rows[s][start]["health"] < 50) / len(eng) if eng else None


def item_timing_hit_rate(B, s, sheet):
    items = [(i["x"], i["y"], TIMED_ITEMS[i["name"]], n) for n, i in enumerate(B.map.get("items", []))
             if i["in_deathmatch"] and i["name"] in TIMED_ITEMS]
    if not items:
        return None
    taken = []
    for k in B.slots:
        for t, kind in B.pickups(k):
            r = B.rows[k][t]
            near = [(math.hypot(x - r["x"], y - r["y"]), n) for x, y, c, n in items if c == kind]
            near = [p for p in near if p[0] <= 64]
            if near:
                taken.append((t, k, min(near)[1]))
    taken.sort()
    last, hits, chances = {}, 0, 0
    for t, k, item in taken:
        if k == s and item in last:
            chances += 1
            if 0 <= t - (last[item] + ITEM_RESPAWN) <= TIMED_SLACK:
                hits += 1
        last[item] = t
    return hits / chances if chances else None


def armor_pickups_rank(B, s, sheet):
    n = {k: sum(1 for _, kind in B.pickups(k) if kind == "armor") for k in B.slots}
    return 1 + sum(1 for k in B.slots if n[k] > n[s])


def behavior_switches_per_minute(B, s, sheet):
    switches = 0
    for t, a, b in B.steps(s):
        if a["behavior"] != b["behavior"] and "RESPAWNING" not in (a["behavior"], b["behavior"]):
            switches += 1
    alive = sum(1 for t in B.displayed() if B.life[s][t] is not None)
    return switches / (alive / (TICRATE * 60)) if alive else None


def _frag_key_share(B, s, keys):
    frags = B.frags_by(s)
    return sum(1 for e in frags if e["key"] in keys) / len(frags) if frags else None


def melee_damage_share(B, s, sheet):
    return _frag_key_share(B, s, {"OB_MPFIST", "OB_MPCHAINSAW"})


def chainsaw_damage_share(B, s, sheet):
    return _frag_key_share(B, s, {"OB_MPCHAINSAW"})


def approach_outside_view_cone_share(B, s, sheet):
    eng = B.engagements(s)
    if not eng:
        return None
    out = 0
    for start, _, tg in eng:
        a, v = B.rows[s][start], B.rows[tg][start]
        bearing = math.degrees(math.atan2(a["y"] - v["y"], a["x"] - v["x"]))
        if abs(wrap180(bearing - v["angle"])) > VIEW_CONE_HALF:
            out += 1
    return out / len(eng)


METRICS = {f.__name__: f for f in (
    median_engagement_distance, moving_forward_share, stationary_share, distance_traveled_rank,
    frags_within_1s_of_visibility_share, engagement_distance_in_band_share, retreat_on_close_share,
    engagements_started_below_50hp_share, item_timing_hit_rate, armor_pickups_rank,
    behavior_switches_per_minute, melee_damage_share, chainsaw_damage_share, approach_outside_view_cone_share)}


def behavior_shares(B, s):
    c = Counter(B.rows[s][t]["behavior"] for t in B.displayed() if B.life[s][t] is not None)
    n = sum(c.values())
    return {k: round(v / n, 4) for k, v in c.most_common()} if n else {}


# ---------------------------------------------------------------------- report
def evaluate(path, sheets):
    B = Bundle(path)
    by_code = {sh["code"]: sh for sh in sheets}
    out = {"bundle": os.path.basename(os.path.normpath(path)), "seed": B.m["seed"], "bots": {}}
    for s in B.slots:
        code = B.code[s]
        sheet = by_code[code]
        sig = []
        for item in sheet.get("signature") or []:
            v = METRICS[item["metric"]](B, s, sheet)
            ok = v is not None and OPS[item["op"]](v, item["value"])
            sig.append({"metric": item["metric"], "value": None if v is None else round(v, 4), "op": item["op"],
                        "threshold": item["value"], "pass": ok})
        out["bots"][code] = {
            "slot": s, "controller": next(b.get("controller") for b in B.m["bots"] if b["slot"] == s),
            "signature": sig, "met": bool(sig) and all(x["pass"] for x in sig),
            "behavior_shares": behavior_shares(B, s),
            "frags": next(r["displayed_frags"] for r in B.m["score"]["rows"] if r["slot"] == s),
            "deaths": next(r["displayed_deaths"] for r in B.m["score"]["rows"] if r["slot"] == s),
            "distance_traveled": round(distance_traveled(B, s)),
        }
    return out


def fmt(v):
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) and v < 10 else f"{v:.0f}" if isinstance(v, float) else str(v))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--bots", help="comma-separated codes to report (default: all eight)")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args(argv)
    sheets = roster.load()
    codes = [c.strip().upper() for c in args.bots.split(",")] if args.bots else [sh["code"] for sh in sheets]
    reports = [evaluate(p, sheets) for p in args.bundles]
    for rep in reports:
        print(f"== {rep['bundle']} (seed {rep['seed']})")
        for code in codes:
            b = rep["bots"][code]
            top = next(iter(b["behavior_shares"]), None)
            print(f"  {code} {'MET ' if b['met'] else 'MISS'}  frags {b['frags']} deaths {b['deaths']}  top {top} "
                  f"{b['behavior_shares'].get(top, 0):.2f}  travelled {b['distance_traveled']}")
            for x in b["signature"]:
                print(f"      {'PASS' if x['pass'] else 'FAIL'}  {x['metric']} = {fmt(x['value'])}  (needs {x['op']} {x['threshold']})")
    if len(reports) > 1:
        print("== summary: matches meeting the whole signature")
        for code in codes:
            met = sum(1 for r in reports if r["bots"][code]["met"])
            per = {}
            for r in reports:
                for x in r["bots"][code]["signature"]:
                    per.setdefault(x["metric"], []).append(x["value"])
            vals = "; ".join(f"{m} [{', '.join(fmt(v) for v in vs)}]" for m, vs in per.items())
            print(f"  {code} {met}/{len(reports)}  {vals}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(reports, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
