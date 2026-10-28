"""Personality bench: one short match with the milestone 2A personalities, no video.

    python -m engine.bench.personality --seed 1234 --out out/bench

VEX, HEX and NUL run the shared personality engine and the other five run placeholders,
as in a match. Over the displayed match it asserts that:
  - the three bots' behavior distributions differ: no two share a top behavior, and every
    pair is at least MIN_DISTANCE apart in total variation;
  - each wrote utility_scores on every alive row and only ever named weapons it resolved;
  - the capture itself is sound (lockstep, events reconcile), as in the smoke bench.
"""

import argparse
import datetime as dt
import os
import sys
from collections import Counter
from itertools import combinations

from engine import bundle, match, personality, roster
from engine.bench.controlled import report
from engine.runner import MILESTONE_2A, build, controllers, preflight
from tools import extract_map

TICS = 2100  # displayed, after the pre-roll
MIN_DISTANCE = 0.3
RESPAWN_DELAY = 2


def shares(rows, slot, f0):
    c = Counter(r["behavior"] for r in rows if r["slot"] == slot and r["tic"] >= f0 and r["alive"])
    n = sum(c.values())
    return {k: v / n for k, v in c.items()} if n else {}


def total_variation(a, b):
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Personality bench: VEX, HEX and NUL must behave differently.")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--port", type=int, default=5033)
    ap.add_argument("--out", default="out/bench")
    args = ap.parse_args(argv)
    err = preflight()
    if err:
        print(f"bench: refusing to run: {err}", file=sys.stderr)
        return 2
    sheets = roster.load()
    codes = set(MILESTONE_2A.split(","))
    iwad = extract_map.default_iwad()
    map_data = extract_map.extract(iwad, "MAP01")
    cache_dir = os.path.join(args.out, ".cache")
    personality.prepare(map_data, cache_dir, [sh for sh in sheets if sh["code"] in codes])
    paths, kwargs, resolved = controllers(sheets, codes, map_data, cache_dir)
    f0 = bundle.frame0_tic(RESPAWN_DELAY, 35)
    total = f0 + TICS
    workdir = os.path.join(args.out, f"personality-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-s{args.seed}")
    print(f"personality bench: {workdir}")
    try:
        capture = match.run_match(sheets=sheets, iwad=iwad, map_name="MAP01", seed=args.seed, duration_tics=total,
                                  port=args.port, respawn_delay=RESPAWN_DELAY, workdir=workdir, controller=paths,
                                  controller_kwargs=kwargs, video=False, log=lambda m: None)
    except match.MatchFailed as e:
        report(f"all 8 processes reach tic {total}", [str(e)])
        return 1
    data, problems, _ = build(capture, total, sheets, respawn_delay=RESPAWN_DELAY)
    ok = report(f"capture: lockstep, mapping {data['numbers']}, events reconcile", problems)

    rows = data["rows"]
    slot_of = {sh["code"]: sh["slot"] for sh in sheets}
    dist = {c: shares(rows, slot_of[c], f0) for c in sorted(codes)}
    for c, d in dist.items():
        top = sorted(d.items(), key=lambda kv: -kv[1])[:4]
        print(f"      {c}: " + ", ".join(f"{k} {v:.2f}" for k, v in top))
    tops = {c: max(d, key=d.get) for c, d in dist.items() if d}
    clash = [f"{a} and {b} both mostly {tops[a]}" for a, b in combinations(sorted(tops), 2) if tops[a] == tops[b]]
    near = [f"{a}/{b} total variation {total_variation(dist[a], dist[b]):.2f}"
            for a, b in combinations(sorted(dist), 2) if total_variation(dist[a], dist[b]) < MIN_DISTANCE]
    missing = [c for c in sorted(codes) if not dist[c]]
    ok &= report(f"behavior distributions differ (distinct top behaviors; pairwise total variation >= {MIN_DISTANCE})",
                 clash + near + [f"{c} has no alive tics" for c in missing])

    bad = []
    for c in sorted(codes):
        s = slot_of[c]
        mine = [r for r in rows if r["slot"] == s and r["alive"]]
        no_scores = sum(1 for r in mine if not r["utility_scores"])
        wrong = {r["weapon_name"] for r in mine if r["weapon_name"] is not None and r["weapon_name"] not in resolved[s]}
        if no_scores:
            bad.append(f"{c}: {no_scores} alive rows without utility_scores")
        if wrong:
            bad.append(f"{c}: weapon_name {sorted(wrong)} outside weapons_resolved {resolved[s]}")
    ok &= report("utility_scores on every alive row; weapon_name only from weapons_resolved", bad)
    print("PERSONALITY BENCH " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
