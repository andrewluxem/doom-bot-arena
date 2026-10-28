"""Fit each gating metric's threshold to an evaluation, against the roster (docs/SIGNATURES.md, milestone 2D).

    python3 tools/fit_thresholds.py bundles/eval-2c/*/2* [--json out.json] [--write --run fit-2d]

For every gating item in the sheets (`gate: true`, op other than `==`), with the op kept, the fitted
threshold is the value that maximizes the bot's pass rate over all its matches subject to at most
MAX_OTHERS (20%) of the other seven personalities' bot-matches passing on each map, measured with the
bot's own sheet. An n/a value of the bot fails; n/a values of the others are left out.

Candidates are the observed values (the bot's and the others'). Ties in the bot's pass rate go to the
lowest worst-map pass rate of the others, then to the candidate nearest the sheet's current value.
The threshold is then moved to the roundest number in its gap, the open interval between neighbouring
observed values in which every value keeps its outcome, nearest the gap's middle: so rounding never
changes a pass or a fail, and the threshold sits between the bot and the others where the data allows.

A metric separates on both maps if a fitted threshold exists and, on each map, the bot passes it more
often than the others. With --write, each separating metric's `value` in characters/*.yaml is replaced
and a comment names the fit run; a metric that does not separate is reported and its sheet left alone.
`==` items (ASH's melee_damage_share == 1.0) are reported, not fitted.

A conjunction (an item with an `and` part, milestone 2E) is fitted jointly: every pair of candidate
thresholds on a grid of the two parts' observed values, both parts required to pass, the others' pass
rate counted on the conjunction. Each part is then rounded inside its own gap.
"""

import argparse
import itertools
import json
import math
import os
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster  # noqa: E402
from tools.signatures import (OPS, Bundle, by_map, item_values, label, parts,  # noqa: E402
                               roster_item_values)

MAX_OTHERS = 0.2


def passes_all(ops, ts, vals):
    """Every part passes; n/a in any part fails."""
    return all(v is not None and OPS[op](v, t) for op, t, v in zip(ops, ts, vals))


def rate(ops, ts, vals, na_fails):
    """Pass rate of value tuples against every part; a tuple with an n/a fails (na_fails) or is left out."""
    vs = vals if na_fails else [v for v in vals if None not in v]
    if not vs:
        return None
    return sum(1 for v in vs if passes_all(ops, ts, v)) / len(vs)


def gap(op, t, values):
    """The thresholds that give every observed value the same outcome as t: (low, high, low_open, high_open)."""
    below = max((v for v in values if v < t), default=-math.inf)
    above = min((v for v in values if v > t), default=math.inf)
    if op in (">", "<="):  # t itself fails `>` (passes `<=`); so does anything up to the next value
        return t, above, False, True
    return below, t, True, False  # `>=`, `<`: anything above the value below t, up to t


def roundest(op, t, values):
    """The roundest number inside t's gap (coarsest step first), nearest the gap's middle; t itself if the gap
    is unbounded on one side or holds no number on the finest step."""
    lo, hi, lo_open, hi_open = gap(op, t, values)
    if math.isinf(lo) or math.isinf(hi):
        return t  # no observed value beyond it: nothing to round between
    mid = (lo + hi) / 2
    for step in (100, 50, 10, 5, 1, 0.5, 0.1, 0.05, 0.01, 0.005, 0.001):
        k0, k1 = math.ceil(lo / step), math.floor(hi / step)
        inside = [round(k * step, 3) for k in range(k0, k1 + 1)
                  if (k * step > lo or (not lo_open and math.isclose(k * step, lo)))
                  and (k * step < hi or (not hi_open and math.isclose(k * step, hi)))
                  and not (lo_open and math.isclose(k * step, lo)) and not (hi_open and math.isclose(k * step, hi))]
        if inside:
            return min(inside, key=lambda v: abs(v - mid))
    return t


def evaluate(ops, ts, own, others):
    """own, others: {map: [value tuples]}. Rates per map and overall."""
    o = {m: rate(ops, ts, vs, False) for m, vs in others.items()}
    b = {m: rate(ops, ts, vs, True) for m, vs in own.items()}
    allown = [v for vs in own.values() for v in vs]
    return {"threshold": list(ts), "bot_pass": rate(ops, ts, allown, True),
            "bot_pass_by_map": b, "others_pass_by_map": o,
            "passes": sum(1 for v in allown if passes_all(ops, ts, v)), "matches": len(allown)}


def part_values(own, others, i):
    return [v[i] for vs in list(own.values()) + list(others.values()) for v in vs if v[i] is not None]


def fit(ops, current, own, others):
    """Joint grid over every part's observed values (an `==` part stays at its current value)."""
    grids = [[c] if op == "==" else sorted(set(part_values(own, others, i)) | {c})
             for i, (op, c) in enumerate(zip(ops, current))]
    best = None
    for ts in itertools.product(*grids):
        e = evaluate(ops, ts, own, others)
        worst = max((r for r in e["others_pass_by_map"].values() if r is not None), default=0.0)
        if worst > MAX_OTHERS:
            continue
        key = (e["bot_pass"], -worst, -sum(abs(t - c) / (abs(c) or 1.0) for t, c in zip(ts, current)))
        if best is None or key > best[0]:
            best = (key, ts)
    if best is None:
        return None
    rounded = [t if op == "==" else roundest(op, t, part_values(own, others, i))
               for i, (op, t) in enumerate(zip(ops, best[1]))]
    return evaluate(ops, rounded, own, others)


def separates(e):
    if e is None:
        return False
    for m, b in e["bot_pass_by_map"].items():
        o = e["others_pass_by_map"][m]
        if o is None or o > MAX_OTHERS or b is None or b <= o:
            return False
    return True


def run(bundles, sheets, codes=None):
    groups = by_map(bundles)
    out = []
    for sh in sheets:
        if codes and sh["code"] not in codes:
            continue
        for idx, item in enumerate(sh.get("signature") or []):
            if not item.get("gate", True):
                continue
            ps = parts(item)
            ops, current = [p["op"] for p in ps], [p["value"] for p in ps]
            own, others = {}, {}
            for key, Bs in groups.items():
                own[key[0]] = [item_values(B, next(s for s in B.slots if B.code[s] == sh["code"]), item, sh) for B in Bs]
                others[key[0]] = roster_item_values(Bs, sh["code"], item, sh)
            row = {"bot": sh["code"], "index": idx, "label": label(item), "metrics": [p["metric"] for p in ps],
                   "ops": ops, "current": current, "at_current": evaluate(ops, current, own, others)}
            if all(op == "==" for op in ops):
                row["fitted"], row["note"] = None, "equality: reported, not fitted"
                row["separates"] = separates(row["at_current"])
            else:
                row["fitted"] = fit(ops, current, own, others)
                row["separates"] = separates(row["fitted"])
                if row["fitted"] is None:
                    row["note"] = "no threshold keeps the others at or under 20% on every map"
                elif not row["separates"]:
                    row["note"] = "the bot does not pass more often than the others on every map"
            out.append(row)
    return out


def pct(r):
    return "n/a" if r is None else f"{r:.0%}"


def show(rows):
    print(f"== fitted thresholds: maximize the bot's pass rate, at most {MAX_OTHERS:.0%} of the other seven passing on each map")
    for r in rows:
        c, f = r["at_current"], r["fitted"]
        line = (f"  {r['bot']} {r['label']}: bot {c['passes']}/{c['matches']}, "
                f"others {', '.join(f'{m} {pct(v)}' for m, v in c['others_pass_by_map'].items())}")
        if f is not None:
            th = " AND ".join(f"{m} {op} {t}" for m, op, t in zip(r["metrics"], r["ops"], f["threshold"]))
            line += (f"\n      -> fitted {th}: bot {f['passes']}/{f['matches']} "
                     f"({', '.join(f'{m} {pct(v)}' for m, v in f['bot_pass_by_map'].items())}), "
                     f"others {', '.join(f'{m} {pct(v)}' for m, v in f['others_pass_by_map'].items())}")
        line += "  SEPARATES" if r["separates"] else f"  does not separate ({r.get('note', '')})"
        print(line)


ITEM = re.compile(r"^  - metric: ", re.M)


def write(rows, sheets, run_name, source):
    """Replace the `value` of each part of each separating fitted item (found by its position in the sheet's
    signature list), with a comment naming the run."""
    files = {sh["code"]: os.path.join(roster.ROOT, sh["_file"]) for sh in sheets}
    for r in rows:
        if r["fitted"] is None or not r["separates"]:
            continue
        path = files[r["bot"]]
        with open(path) as f:
            text = f.read()
        sig = text.index("\nsignature:\n")
        starts = [m.start() for m in ITEM.finditer(text, sig)] + [len(text)]
        lo, hi = starts[r["index"]], starts[r["index"] + 1]
        block = text[lo:hi]
        for depth, (old, new) in enumerate(zip(r["current"], r["fitted"]["threshold"])):
            indent = " " * (4 + 2 * depth)  # the item's own value, then its `and` part's
            pat = re.compile(r"^" + indent + r"value: [^\n]*$", re.M)
            block, n = pat.subn(f"{indent}value: {new}   # {run_name}: fitted on {source} "
                                f"(tools/fit_thresholds.py); was {old}", block, count=1)
            if n != 1:
                raise SystemExit(f"{path}: no `value` line for item {r['index']} part {depth}")
        text = text[:lo] + block + text[hi:]
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {r['bot']} item {r['index']}: {r['fitted']['threshold']} to {os.path.relpath(path, roster.ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--bots", help="comma-separated codes to fit (default: all eight)")
    ap.add_argument("--write", action="store_true", help="write separating fitted thresholds into the sheets")
    ap.add_argument("--run", default="fit-2d", help="name of the fit run, for the sheet comments")
    ap.add_argument("--source", default="eval-2c", help="name of the evaluation fitted on, for the sheet comments")
    args = ap.parse_args(argv)
    sheets = roster.load()
    codes = [c.strip().upper() for c in args.bots.split(",")] if args.bots else None
    rows = run([Bundle(p) for p in args.bundles], sheets, codes)
    show(rows)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"run": args.run, "source": args.source, "max_others": MAX_OTHERS, "fits": rows}, f, indent=1)
    if args.write:
        write(rows, sheets, args.run, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
