"""Signature metrics per bot, from match bundles (docs/SIGNATURES.md).

    python3 tools/signatures.py bundles/<id> [bundles/<id> ...] [--bots VEX,HEX,NUL] [--compare] [--json out.json]
    python3 tools/signatures.py --json out.json bundles/eval-2c/*/2*

When every bot in the bundles is a personality (milestone 2C), separation is per map against the
roster: for a bot's metric, the other seven personalities' bot-matches on the same map and IWAD,
measured against the bot's threshold. A metric separates on a map if at least 80% of them fail it;
it gates if it separates on every map evaluated and the bot meets it in 80% of its matches. The
report then also gives every metric for all eight bots against the other seven, and the top
behaviors shared between bots.

Every metric is computed for all eight bots, so ranks are defined. Each bot is then
checked against the thresholds in its own characters/*.yaml signature block. Items marked
`gate: false` are descriptive: reported, never part of whether the signature is met.

Bundles with placeholders (milestone 2A) use the placeholder rule instead. A summary counts the
matches in which each bot met its gating metrics. A separation section then prints, for each
reported bot's metrics, its own values and the placeholders' values against the same threshold, and the placeholder pass rate: a gating metric must
fail for at least 80% of placeholder bot-matches in the same evaluation (SEPARATION).
--compare also prints the metrics for every bot, the other personalities included.
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

from engine import items as itemfacts, roster  # noqa: E402

TICRATE = 35
TELEPORT_STEP = 64.0
WINDOW = 35
STATIONARY_RADIUS = 32.0
VIEW_CONE_HALF = 45.0
RETREAT_MIN = 48.0
ITEM_RESPAWN = itemfacts.RESPAWN_TICS
TIMED_SLACK = 70
TIMED_ITEMS = itemfacts.TIMED
HOLD_RADIUS = 16.0
HOLD_TICS = 70
BUSY_TICS = 35  # third party: the victim named another slot as its target in the second before the frag
HURT_TICS = 105  # ...or took damage from another slot in the 3 s before it
CONTACT_GAP = 35  # a player back in view after this long out of it is a new contact
SEPARATION = 0.8
PLACEHOLDER = "engine.bots:PlaceholderBot"
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
        self.map_key = (self.m.get("map"), self.m.get("iwad", {}).get("sha256", ""))
        self.code = {b["slot"]: b["code"] for b in self.m["bots"]}
        # schema 1 bundles predate personalities: every bot was a placeholder
        self.controller = {b["slot"]: b.get("controller", PLACEHOLDER) for b in self.m["bots"]}
        self.rows = {s: [None] * self.end for s in self.slots}
        for r in read_jsonl(os.path.join(path, "tics.jsonl")):
            self.rows[r["slot"]][r["tic"]] = r
        self.events = read_jsonl(os.path.join(path, "events.jsonl"))
        self._map = None
        self._sight = None
        self.life = {s: self._lives(s) for s in self.slots}
        self.memo = {}  # (method, slot) -> result: every method below is pure for a bundle

    def _cached(self, name, s, fn):
        key = (name, s)
        if key not in self.memo:
            self.memo[key] = fn()
        return self.memo[key]

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
        return self._cached("steps", s, lambda: list(self._steps(s)))

    def _steps(self, s):
        rows, life = self.rows[s], self.life[s]
        for t in range(self.f0 + 1, self.end):
            if life[t] is None or life[t] != life[t - 1]:
                continue
            a, b = rows[t - 1], rows[t]
            if math.hypot(b["x"] - a["x"], b["y"] - a["y"]) <= TELEPORT_STEP:
                yield t, a, b

    def engagement_tics(self, s):
        return self._cached("engagement_tics", s, lambda: self._engagement_tics(s))

    def _engagement_tics(self, s):
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
        return self._cached("engagements", s, lambda: self._engagements(s))

    def _engagements(self, s):
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
        return self._cached("pickups", s, lambda: self._pickups(s))

    def _pickups(self, s):
        out = []
        for t, a, b in self.steps(s):
            if b["armor"] > a["armor"]:
                out.append((t, "armor"))
            if b["health"] > a["health"]:
                out.append((t, "health"))
        return out


    def hurt_by_others(self, v, s):
        """Tics at which v took damage while some slot other than v and s dealt damage (schema 3)."""
        return self._cached("hurt_by_others", (v, s), lambda: self._hurt_by_others(v, s))

    def _hurt_by_others(self, v, s):
        out = set()
        if "damage_taken" not in self.rows[v][0]:
            return out
        others = [k for k in self.slots if k not in (v, s)]
        for t in range(1, self.end):
            if self.rows[v][t]["damage_taken"] > self.rows[v][t - 1]["damage_taken"] and any(
                    self.rows[k][t]["damagecount"] > self.rows[k][t - 1]["damagecount"] for k in others):
                out.add(t)
        return out

    def busy_elsewhere(self, v, s, t):
        """v named a target other than s at some tic in [t - BUSY_TICS, t - 1], or took damage from another
        slot (not s) in [t - HURT_TICS, t - 1]."""
        if any(self.rows[v][k]["target_slot"] not in (None, s) for k in range(max(0, t - BUSY_TICS), t)):
            return True
        hurt = self.hurt_by_others(v, s)
        return any(k in hurt for k in range(max(1, t - HURT_TICS), t))

    def in_view(self, s):
        """{other slot: [bool per tic]}: the other player alive and in s's view and line of sight (schema 2)."""
        return self._cached("in_view", s, lambda: self._in_view(s))

    def _in_view(self, s):
        from tools.hold_contact import in_view
        from engine.geometry import LineOfSight
        if self._sight is None:
            self._sight = (LineOfSight(self.map), [sec["floor"] for sec in self.map["sectors"]])
        sight, floor = self._sight
        out = {}
        for o in self.slots:
            if o == s:
                continue
            seen = [False] * self.end
            for t in self.displayed():
                me, other = self.rows[s][t], self.rows[o][t]
                if me["alive"] and other["alive"] and math.hypot(other["x"] - me["x"], other["y"] - me["y"]) <= 2048:
                    seen[t] = in_view(sight, floor, me, other)
            out[o] = seen
        return out


# ---------------------------------------------------------------------- metrics
def median_engagement_distance(B, s, sheet):
    d = sorted(x[2] for x in B.engagement_tics(s))
    if len(d) < WINDOW:
        return None
    n = len(d)
    return d[n // 2] if n % 2 else (d[n // 2 - 1] + d[n // 2]) / 2


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _dist(a, b):
    return math.hypot(b["x"] - a["x"], b["y"] - a["y"])


MELEE_KEYS = ("OB_MPFIST", "OB_MPCHAINSAW")


def median_kill_distance(B, s, sheet):
    """Over ranged kills only (milestone 2D): a fist or chainsaw kill, by its obituary key, is left out."""
    return _median(_dist(B.rows[s][e["tic"]], B.rows[e["target"]][e["tic"]]) for e in B.frags_by(s)
                   if e.get("key") not in MELEE_KEYS)


def closing_share(B, s, sheet):
    n = closing = 0
    for t, a, b in B.steps(s):
        tg = b["target_slot"]
        if b["behavior"] != "IN_COMBAT" or tg is None or a["target_slot"] != tg:
            continue
        o0, o1 = B.rows[tg][t - 1], B.rows[tg][t]
        if not (o0["alive"] and o1["alive"]):
            continue
        n += 1
        closing += _dist(b, o1) < _dist(a, o0)
    return closing / n if n else None


def median_first_sight_engagement_distance(B, s, sheet):
    return _median(_dist(B.rows[s][start], B.rows[tg][start]) for start, _, tg in B.engagements(s)
                   if B.rows[tg][start]["alive"])


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
    return B._cached("distance", s, lambda: sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for _, a, b in B.steps(s)))


def distance_traveled_rank(B, s, sheet):
    d = {k: distance_traveled(B, k) for k in B.slots}
    return 1 + sum(1 for k in B.slots if d[k] < d[s])


def frags_within_visibility_share(B, s, within_tics):
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
        if k - r <= within_tics:
            ok += 1
    return ok / len(frags)


def frags_within_1500ms_of_visibility_share(B, s, sheet):
    return frags_within_visibility_share(B, s, 1.5 * TICRATE)


def kills_from_hold_share(B, s, sheet):
    kills = B.frags_by(s)
    if not kills:
        return None
    rows, life, held = B.rows[s], B.life[s], 0
    for e in kills:
        k = e["tic"]
        if life[k] is None or k < HOLD_TICS or life[k - HOLD_TICS] != life[k]:
            continue
        held += all(_dist(rows[t], rows[k]) <= HOLD_RADIUS for t in range(k - HOLD_TICS, k))
    return held / len(kills)


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


def _damage_share(B, s, held):
    """Share of the damage s dealt (schema 3 damagecount: other players and barrels) dealt while held(row at t - 1)."""
    if "damagecount" not in B.rows[s][0]:
        return None
    total = part = 0
    for t in range(B.f0 + 1, B.end):
        a, b = B.rows[s][t - 1], B.rows[s][t]
        d = b["damagecount"] - a["damagecount"]
        if d > 0:
            total += d
            part += d if held(a) else 0
    return part / total if total else None


def melee_damage_share(B, s, sheet):
    return _damage_share(B, s, lambda r: r["weapon"] == 1)


def chainsaw_damage_share(B, s, sheet):
    return _damage_share(B, s, lambda r: r["weapon"] == 1 and r["weapon_name"] == "chainsaw")


def first_hit(B, s, start, end, tg):
    """First tic in the engagement where s lands a hit and its target takes damage, or None."""
    for t in range(max(start, 1), end + 1):
        a, b = B.rows[s][t - 1], B.rows[s][t]
        if b["hitcount"] > a["hitcount"] and B.rows[tg][t]["damage_taken"] > B.rows[tg][t - 1]["damage_taken"]:
            return t
    return None


def approach_outside_view_cone_share(B, s, sheet):
    if "hitcount" not in B.rows[s][0]:
        return None
    n = out = 0
    for start, end, tg in B.engagements(s):
        t = first_hit(B, s, start, end, tg)
        if t is None:
            continue
        a, v = B.rows[s][t], B.rows[tg][t - 1]
        bearing = math.degrees(math.atan2(a["y"] - v["y"], a["x"] - v["x"]))
        n += 1
        out += abs(wrap180(bearing - v["angle"])) > VIEW_CONE_HALF
    return out / n if n else None


def third_party_kill_share(B, s, sheet):
    """Share of the bot's kills whose victim was busy with someone else: it named another slot as its target in
    the second before the frag, or took damage from another slot in the 3 s before it (schema 3)."""
    kills = B.frags_by(s)
    if not kills or "damage_taken" not in B.rows[s][0]:
        return None
    return sum(1 for e in kills if B.busy_elsewhere(e["target"], s, e["tic"])) / len(kills)


def first_contact_avoidance_share(B, s, sheet):
    """Over the bot's first contacts (another player coming into its view and line of sight after CONTACT_GAP
    tics out of it, while not busy with anyone else), the share the bot does not name as its target within
    the next 35 tics. Sight is reconstructed as in tools/hold_contact.py (schema 3)."""
    if "damage_taken" not in B.rows[s][0]:
        return None
    n = avoided = 0
    for o, seen in B.in_view(s).items():
        last = -10 ** 9
        for t in B.displayed():
            if not seen[t]:
                continue
            if t - last > CONTACT_GAP and not B.busy_elsewhere(o, s, t):
                n += 1
                end = min(B.end, t + WINDOW + 1)
                avoided += not any(B.rows[s][k]["target_slot"] == o for k in range(t, end))
            last = t
    return avoided / n if n else None


METRICS = {f.__name__: f for f in (
    median_engagement_distance, median_kill_distance, closing_share, median_first_sight_engagement_distance,
    stationary_share, distance_traveled_rank, frags_within_1500ms_of_visibility_share, kills_from_hold_share,
    engagement_distance_in_band_share, retreat_on_close_share,
    engagements_started_below_50hp_share, item_timing_hit_rate, armor_pickups_rank,
    behavior_switches_per_minute, melee_damage_share, chainsaw_damage_share, approach_outside_view_cone_share,
    third_party_kill_share, first_contact_avoidance_share)}


SHEET_PARAMS = {"engagement_distance_in_band_share": lambda sh: tuple(sh["temperament"]["preferred_range"]),
                "retreat_on_close_share": lambda sh: sh["temperament"].get("retreat_distance")}


def value(B, s, metric, sheet):
    """METRICS[metric](B, s, sheet), memoized per bundle; only two metrics read the sheet."""
    memo = getattr(B, "memo", None)
    if memo is None:
        return METRICS[metric](B, s, sheet)
    key = ("metric", s, metric, SHEET_PARAMS[metric](sheet) if metric in SHEET_PARAMS else None)
    if key not in memo:
        memo[key] = METRICS[metric](B, s, sheet)
    return memo[key]


def behavior_shares(B, s):
    c = Counter(B.rows[s][t]["behavior"] for t in B.displayed() if B.life[s][t] is not None)
    n = sum(c.values())
    return {k: round(v / n, 4) for k, v in c.most_common()} if n else {}


# ---------------------------------------------------------------------- report
def passes(item, v):
    return v is not None and OPS[item["op"]](v, item["value"])


def evaluate(B, sheets):
    by_code = {sh["code"]: sh for sh in sheets}
    out = {"bundle": os.path.basename(os.path.normpath(B.path)), "seed": B.m["seed"], "bots": {}}
    for s in B.slots:
        code = B.code[s]
        sheet = by_code[code]
        sig = []
        for item in sheet.get("signature") or []:
            v = value(B, s, item["metric"], sheet)
            sig.append({"metric": item["metric"], "value": None if v is None else round(v, 4), "op": item["op"],
                        "threshold": item["value"], "gate": item.get("gate", True), "pass": passes(item, v)})
        gates = [x for x in sig if x["gate"]]
        out["bots"][code] = {
            "slot": s, "controller": B.controller[s],
            "signature": sig, "met": bool(gates) and all(x["pass"] for x in gates),
            "behavior_shares": behavior_shares(B, s),
            "frags": next(r["displayed_frags"] for r in B.m["score"]["rows"] if r["slot"] == s),
            "deaths": next(r["displayed_deaths"] for r in B.m["score"]["rows"] if r["slot"] == s),
            "distance_traveled": round(distance_traveled(B, s)),
        }
    return out


def separation(bundles, sheets, codes):
    """Per signature metric of each reported bot: its own values, the placeholders' values
    against the same threshold, and whether the metric may gate (docs/SIGNATURES.md)."""
    by_code = {sh["code"]: sh for sh in sheets}
    out = []
    for code in codes:
        for item in by_code[code].get("signature") or []:
            own, others = [], {}
            for B in bundles:
                for s in B.slots:
                    # metrics with sheet parameters (preferred_range, retreat_distance) use the personality's
                    sheet = by_code[code]
                    if B.code[s] == code:
                        own.append(value(B, s, item["metric"], sheet))
                    elif B.controller[s] == PLACEHOLDER:
                        others.setdefault(B.code[s], []).append(value(B, s, item["metric"], sheet))
            computed = [v for vs in others.values() for v in vs if v is not None]
            p_pass = sum(passes(item, v) for v in computed)
            own_pass = sum(passes(item, v) for v in own)
            fail_rate = 1 - p_pass / len(computed) if computed else None
            separates = fail_rate is not None and fail_rate >= SEPARATION
            out.append({
                "bot": code, "metric": item["metric"], "op": item["op"], "threshold": item["value"],
                "gate": item.get("gate", True),
                "personality": {"values": [None if v is None else round(v, 4) for v in own], "passes": own_pass},
                "placeholders": {"values": {c: [None if v is None else round(v, 4) for v in vs] for c, vs in others.items()},
                                 "computed": len(computed), "na": sum(len(vs) for vs in others.values()) - len(computed),
                                 "passes": p_pass, "pass_rate": None if not computed else round(p_pass / len(computed), 4)},
                "separates": separates,
                "can_gate": separates and bool(own) and own_pass / len(own) >= SEPARATION,
            })
    return out


RANK_METRICS = ("distance_traveled_rank", "armor_pickups_rank")


def by_map(bundles):
    out = {}
    for B in bundles:
        out.setdefault(B.map_key, []).append(B)
    return out


def roster_values(Bs, code, metric, sheet):
    """The metric for every other personality's slot in these bundles, with `code`'s sheet."""
    return [value(B, s, metric, sheet) for B in Bs for s in B.slots
            if B.code[s] != code and B.controller[s] != PLACEHOLDER]


def separation_vs_roster(bundles, sheets, codes):
    """Per bot and signature item: on each map, the bot's values and the other seven personalities' pass rate
    against the same threshold (the roster baseline). A metric separates on a map if at least 80% of the other
    personalities' bot-matches there fail it; it gates if it separates on every map evaluated and the bot
    passes it in at least 80% of its matches (docs/SIGNATURES.md, separation)."""
    by_code = {sh["code"]: sh for sh in sheets}
    groups = by_map(bundles)
    out = []
    for code in codes:
        sheet = by_code[code]
        for item in sheet.get("signature") or []:
            row = {"bot": code, "metric": item["metric"], "op": item["op"], "threshold": item["value"],
                   "sheet_gate": item.get("gate", True), "rank_metric": item["metric"] in RANK_METRICS, "maps": {}}
            for key, Bs in groups.items():
                own = [value(B, next(s for s in B.slots if B.code[s] == code), item["metric"], sheet) for B in Bs]
                rv = roster_values(Bs, code, item["metric"], sheet)
                computed = [v for v in rv if v is not None]
                r_pass = sum(passes(item, v) for v in computed)
                fail_rate = 1 - r_pass / len(computed) if computed else None
                row["maps"][key[0]] = {
                    "values": [None if v is None else round(v, 4) for v in own],
                    "seeds": [B.m["seed"] for B in Bs], "passes": sum(passes(item, v) for v in own), "matches": len(own),
                    "roster": {"computed": len(computed), "na": len(rv) - len(computed), "passes": r_pass,
                               "pass_rate": None if not computed else round(r_pass / len(computed), 4),
                               "median": _median(computed),
                               "range": [min(computed), max(computed)] if computed else None},
                    "separates": fail_rate is not None and fail_rate >= SEPARATION}
            maps = row["maps"].values()
            row["passes"] = sum(m["passes"] for m in maps)
            row["matches"] = sum(m["matches"] for m in maps)
            row["separates_on"] = [k for k, m in row["maps"].items() if m["separates"]]
            row["gates"] = (len(row["separates_on"]) == len(row["maps"])
                            and row["passes"] >= SEPARATION * row["matches"])
            out.append(row)
    return out


def metric_matrix(bundles, sheets):
    """Every metric for every bot (median over its matches per map), against the median and range of the other
    seven personalities on that map, all computed with the bot's own sheet."""
    groups = by_map(bundles)
    out = {}
    for metric in METRICS:
        out[metric] = {}
        for sh in sheets:
            per = {}
            for key, Bs in groups.items():
                own = [value(B, next(s for s in B.slots if B.code[s] == sh["code"]), metric, sh) for B in Bs]
                rv = [v for v in roster_values(Bs, sh["code"], metric, sh) if v is not None]
                own_c = [v for v in own if v is not None]
                per[key[0]] = {"median": _median(own_c), "n": len(own_c),
                               "roster_median": _median(rv), "roster_range": [min(rv), max(rv)] if rv else None}
            out[metric][sh["code"]] = per
    return out


MIN_TV = 0.3  # the personality bench's MIN_DISTANCE


def total_variation(a, b):
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def behavior_distances(reports):
    """Per pair of bots: total variation distance between their behavior distributions in each match, and in how
    many matches it is at least MIN_TV (milestone 2D acceptance: in 80% of matches, for every pair)."""
    codes = sorted({c for r in reports for c in r["bots"]})
    out = {}
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            tv = [round(total_variation(r["bots"][a]["behavior_shares"], r["bots"][b]["behavior_shares"]), 4)
                  for r in reports]
            far = sum(1 for d in tv if d >= MIN_TV)
            out[f"{a}/{b}"] = {"tv": tv, "far": far, "matches": len(tv), "ok": far >= SEPARATION * len(tv)}
    return out


def shared_tops(reports):
    """Top behavior per bot per match, and per pair of bots the matches in which their tops are the same."""
    tops = {r["bundle"]: {c: next(iter(b["behavior_shares"]), None) for c, b in r["bots"].items()} for r in reports}
    codes = sorted({c for t in tops.values() for c in t})
    pairs = {}
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            n = sum(1 for t in tops.values() if t[a] is not None and t[a] == t[b])
            if n:
                pairs[f"{a}/{b}"] = n
    return tops, pairs


def report_vs_roster(sep, matrix, reports, sheets):
    print("== separation per map against the roster (a metric separates on a map if at least "
          f"{SEPARATION:.0%} of the other seven personalities' bot-matches there fail it; n/a left out)")
    for x in sep:
        status = "GATES" if x["gates"] else "does not gate"
        print(f"  {x['bot']} {x['metric']} {x['op']} {x['threshold']}  [sheet {'gate' if x['sheet_gate'] else 'desc'}]  "
              f"met {x['passes']}/{x['matches']}; separates on {', '.join(x['separates_on']) or 'no map'}: {status}")
        for mp, m in x["maps"].items():
            r = m["roster"]
            print(f"      {mp}: {x['bot']} {', '.join(fmt(v) for v in m['values'])} (pass {m['passes']}/{m['matches']})  "
                  f"roster pass {r['passes']}/{r['computed']}" + (f" ({r['pass_rate']:.0%})" if r["pass_rate"] is not None else "")
                  + (f", n/a {r['na']}" if r["na"] else "")
                  + (f", median {fmt(r['median'])}, range {fmt(r['range'][0])}-{fmt(r['range'][1])}" if r["range"] else ""))
    print("== acceptance: bots with at least one metric that separates on every map and is met in "
          f"{SEPARATION:.0%} of matches")
    for sh in sheets:
        rows = [x for x in sep if x["bot"] == sh["code"]]
        ok = [x["metric"] for x in rows if x["gates"]]
        print(f"  {sh['code']}: " + (f"ACCEPTED by {', '.join(ok)}" if ok else "not accepted"))
    dist = behavior_distances(reports)
    print(f"== behavior distributions: every pair must be at least {MIN_TV} apart in total variation in "
          f"{SEPARATION:.0%} of matches")
    for pair, x in sorted(dist.items(), key=lambda kv: kv[1]["far"]):
        print(f"  {pair}: {x['far']}/{x['matches']}{'' if x['ok'] else '  UNDER'}  min {min(x['tv']):.2f}  "
              f"median {_median(x['tv']):.2f}")
    bad = [p for p, x in dist.items() if not x["ok"]]
    print(f"  {'all pairs apart' if not bad else f'{len(bad)} pair(s) under: ' + ', '.join(bad)}")
    tops, pairs = shared_tops(reports)
    print("== top behavior per bot per match (report only; not part of acceptance since milestone 2D)")
    for bundle, t in tops.items():
        print(f"  {bundle}: " + ", ".join(f"{c} {b}" for c, b in t.items()))
    print("  pairs sharing a top behavior: " + ", ".join(f"{p} {n}" for p, n in sorted(pairs.items(), key=lambda kv: -kv[1])))
    print("== every metric for every bot: median over its matches per map | other seven's median (range), same sheet")
    for metric, per_bot in matrix.items():
        print(f"  {metric}")
        for code, per in per_bot.items():
            print(f"      {code}  " + "   ".join(
                f"{mp} {fmt(v['median'])} | {fmt(v['roster_median'])}"
                + (f" ({fmt(v['roster_range'][0])}-{fmt(v['roster_range'][1])})" if v["roster_range"] else "")
                for mp, v in per.items()))


def fmt(v):
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) and v < 10 else f"{v:.0f}" if isinstance(v, float) else str(v))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--bots", help="comma-separated codes to report (default: all eight)")
    ap.add_argument("--compare", action="store_true", help="print the reported bots' metrics for all eight bots")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args(argv)
    sheets = roster.load()
    codes = [c.strip().upper() for c in args.bots.split(",")] if args.bots else [sh["code"] for sh in sheets]
    bundles = [Bundle(p) for p in args.bundles]
    reports = [evaluate(B, sheets) for B in bundles]
    if all(B.controller[s] != PLACEHOLDER for B in bundles for s in B.slots):
        for rep in reports:
            print(f"== {rep['bundle']} (seed {rep['seed']})")
            for code in codes:
                b = rep["bots"][code]
                top = next(iter(b["behavior_shares"]), None)
                print(f"  {code} frags {b['frags']} deaths {b['deaths']}  top {top} {b['behavior_shares'].get(top, 0):.2f}  "
                      + "  ".join(f"{x['metric']} {fmt(x['value'])}{'' if x['pass'] else ' (fails)'}" for x in b["signature"]))
        sep = separation_vs_roster(bundles, sheets, codes)
        matrix = metric_matrix(bundles, sheets)
        report_vs_roster(sep, matrix, reports, [sh for sh in sheets if sh["code"] in codes])
        if args.json:
            tops, pairs = shared_tops(reports)
            with open(args.json, "w") as f:
                json.dump({"matches": reports, "separation": sep, "matrix": matrix, "top_behaviors": tops,
                           "shared_top_pairs": pairs, "behavior_distances": behavior_distances(reports)}, f, indent=1)
        return 0
    sep = separation(bundles, sheets, codes)
    for rep in reports:
        print(f"== {rep['bundle']} (seed {rep['seed']})")
        for code in codes:
            b = rep["bots"][code]
            top = next(iter(b["behavior_shares"]), None)
            print(f"  {code} {'MET ' if b['met'] else 'MISS'}  frags {b['frags']} deaths {b['deaths']}  top {top} "
                  f"{b['behavior_shares'].get(top, 0):.2f}  travelled {b['distance_traveled']}")
            for x in b["signature"]:
                print(f"      {'PASS' if x['pass'] else 'FAIL'}  {'gate' if x['gate'] else 'desc'}  "
                      f"{x['metric']} = {fmt(x['value'])}  (needs {x['op']} {x['threshold']})")
    print("== summary: matches meeting the gating metrics (desc = descriptive, does not gate)")
    for code in codes:
        met = sum(1 for r in reports if r["bots"][code]["met"])
        rows = [x for x in sep if x["bot"] == code]
        blocked = [x["metric"] for x in rows if x["gate"] and not x["can_gate"]]
        print(f"  {code} {met}/{len(reports)}" + (f"  gating metrics that do not qualify here: {', '.join(blocked)}" if blocked else ""))
    print(f"== separation: a gating metric must pass for its personality and fail for at least "
          f"{SEPARATION:.0%} of placeholder bot-matches (n/a left out)")
    for x in sep:
        p = x["placeholders"]
        verdict = "can gate" if x["can_gate"] else "cannot gate"
        print(f"  {x['bot']} {x['metric']} {x['op']} {x['threshold']}  [{'gate' if x['gate'] else 'desc'}]  "
              f"placeholders pass {p['passes']}/{p['computed']}"
              + (f" ({p['pass_rate']:.0%})" if p["pass_rate"] is not None else "")
              + (f", n/a {p['na']}" if p["na"] else "")
              + f"  {'separates' if x['separates'] else 'does not separate'}; {verdict}")
        print(f"      {x['bot']:<4} {', '.join(fmt(v) for v in x['personality']['values'])}  "
              f"pass {x['personality']['passes']}/{len(x['personality']['values'])}")
        for c, vs in p["values"].items():
            print(f"      {c:<4} {', '.join(fmt(v) for v in vs)}")
    if args.compare:
        compare(bundles, sheets, codes)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"matches": reports, "separation": sep}, f, indent=1)
    return 0


def compare(bundles, sheets, codes):
    """Each reported bot's signature metrics, computed for every bot (personality and placeholder)."""
    by_code = {sh["code"]: sh for sh in sheets}
    metrics = []
    for code in codes:
        for item in by_code[code].get("signature") or []:
            if item["metric"] not in metrics:
                metrics.append(item["metric"])
    print("== every bot on these metrics (one value per bundle)")
    for metric in metrics:
        users = [c for c in codes if any(i["metric"] == metric for i in by_code[c].get("signature") or [])]
        print(f"  {metric}  (signature of {', '.join(users)})")
        for sh in sheets:
            vals = []
            for B in bundles:
                s = next(k for k in B.slots if B.code[k] == sh["code"])
                vals.append(value(B, s, metric, sh))
            print(f"      {sh['code']}{'*' if sh['code'] in users else ' '} {', '.join(fmt(v) for v in vals)}")


if __name__ == "__main__":
    sys.exit(main())
