"""Item arrival latency: how long after a timed item respawned a bot took it, from match bundles.

    python3 tools/item_latency.py bundles/eval-2d/*/2* [--bots KRN] [--json out.json]

Pickups are attributed to timed items as in docs/SIGNATURES.md (item_timing_hit_rate): every pickup by
any bot goes to the nearest timed item of its kind within 64 units. For each of the bot's pickups of an
item whose previous pickup in the match is known, the latency is the pickup tic minus that pickup + 1050
(the respawn). Within [0, 70] is a timed pickup, the metric's hit; the rest of the distribution shows how
late the others were.
"""

import argparse
import json
import math
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.signatures import ITEM_RESPAWN, TIMED_ITEMS, TIMED_SLACK, Bundle, _median  # noqa: E402


def latencies(B, code):
    items = [(i["x"], i["y"], TIMED_ITEMS[i["name"]], n) for n, i in enumerate(B.map.get("items", []))
             if i["in_deathmatch"] and i["name"] in TIMED_ITEMS]
    taken = []
    for k in B.slots:
        for t, kind in B.pickups(k):
            r = B.rows[k][t]
            near = [(math.hypot(x - r["x"], y - r["y"]), n) for x, y, c, n in items if c == kind]
            near = [p for p in near if p[0] <= 64]
            if near:
                taken.append((t, B.code[k], min(near)[1]))
    taken.sort()
    last, out, first = {}, [], 0
    for t, c, n in taken:
        if c == code:
            if n in last:
                out.append(t - last[n] - ITEM_RESPAWN)
            else:
                first += 1
        last[n] = t
    return out, first


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--bots", default="KRN")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    report = {}
    for code in [c.strip().upper() for c in args.bots.split(",")]:
        per = []
        for p in args.bundles:
            B = Bundle(p)
            lat, first = latencies(B, code)
            hits = sum(1 for d in lat if 0 <= d <= TIMED_SLACK)
            per.append({"bundle": os.path.basename(os.path.normpath(p)), "map": B.m["map"], "seed": B.m["seed"],
                        "latencies": lat, "timed": hits, "first_pickups": first})
            print(f"{code} {B.m['map']} seed {B.m['seed']}: {len(lat)} pickups of a respawned item, {hits} within "
                  f"{TIMED_SLACK} tics; median {_median(lat) if lat else 'n/a'}; latencies {sorted(lat)}")
        for mp in sorted({x["map"] for x in per}):
            lat = [d for x in per if x["map"] == mp for d in x["latencies"]]
            hits = sum(1 for d in lat if 0 <= d <= TIMED_SLACK)
            print(f"{code} {mp}: {hits}/{len(lat)} within {TIMED_SLACK} tics of the respawn; median latency "
                  f"{_median(lat) if lat else 'n/a'} tics; {sum(1 for d in lat if d > 650)} over 650")
        report[code] = per
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
