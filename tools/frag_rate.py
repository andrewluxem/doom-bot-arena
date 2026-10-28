"""Frags per minute per map, from match bundles.

    python3 tools/frag_rate.py bundles/baseline-2b/*/2* [--json out.json]

A frag is a credited `frag` event in the displayed match (any key, barrels included);
telefrags and suicides are counted separately and are not frags. The rate is per match,
over the displayed minutes, for all eight bots together; the per-map line gives the mean
and the range over that map's matches.
"""

import argparse
import json
import os
import sys

TICRATE = 35


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def rates(path):
    with open(os.path.join(path, "match.json")) as f:
        m = json.load(f)
    f0, n = m["frame0_tic"], m["duration_tics"]
    minutes = n / TICRATE / 60
    shown = [e for e in read_jsonl(os.path.join(path, "events.jsonl")) if f0 <= e["tic"] < f0 + n]
    count = lambda kind: sum(1 for e in shown if e["type"] == kind and (kind != "frag" or e["credited"]))
    controllers = sorted({b.get("controller", "engine.bots:PlaceholderBot").rsplit(":", 1)[-1] for b in m["bots"]})
    return {"bundle": os.path.basename(os.path.normpath(path)), "map": m["map"], "seed": m["seed"],
            "controllers": controllers, "minutes": round(minutes, 3),
            "frags": count("frag"), "suicides": count("suicide"), "telefrags": count("telefrag"),
            "frags_per_minute": round(count("frag") / minutes, 3)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    per = [rates(p) for p in args.bundles]
    by_map = {}
    for r in per:
        by_map.setdefault(r["map"], []).append(r)
    out = {}
    for mp, rs in by_map.items():
        fpm = [r["frags_per_minute"] for r in rs]
        out[mp] = {"matches": len(rs), "mean_frags_per_minute": round(sum(fpm) / len(fpm), 3),
                   "min": min(fpm), "max": max(fpm), "suicides": sum(r["suicides"] for r in rs),
                   "telefrags": sum(r["telefrags"] for r in rs)}
        print(f"{mp}: {out[mp]['mean_frags_per_minute']:.2f} frags/min over {len(rs)} matches "
              f"(range {min(fpm):.2f}-{max(fpm):.2f}); suicides {out[mp]['suicides']}, telefrags {out[mp]['telefrags']}")
        for r in sorted(rs, key=lambda r: r["seed"]):
            print(f"    seed {r['seed']}: {r['frags']} frags in {r['minutes']:.1f} min = {r['frags_per_minute']:.2f}/min, "
                  f"suicides {r['suicides']}  [{', '.join(r['controllers'])}]")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"maps": out, "matches": per}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
