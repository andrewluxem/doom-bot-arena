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
"""

import argparse
import json
import math
import os
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster  # noqa: E402
from tools.signatures import OPS, Bundle, by_map, roster_values, value  # noqa: E402

MAX_OTHERS = 0.2


def rate(item, op, t, vals, na_fails):
    """Pass rate of vals against `op t`; n/a fails (na_fails) or is left out."""
    vs = vals if na_fails else [v for v in vals if v is not None]
    if not vs:
        return None
    return sum(1 for v in vs if v is not None and OPS[op](v, t)) / len(vs)


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


def evaluate(op, t, own, others):
    """own, others: {map: [values]}. Rates per map and overall."""
    o = {m: rate(None, op, t, vs, False) for m, vs in others.items()}
    b = {m: rate(None, op, t, vs, True) for m, vs in own.items()}
    allown = [v for vs in own.values() for v in vs]
    return {"threshold": t, "bot_pass": rate(None, op, t, allown, True),
            "bot_pass_by_map": b, "others_pass_by_map": o,
            "passes": sum(1 for v in allown if v is not None and OPS[op](v, t)), "matches": len(allown)}


def fit(op, current, own, others):
    cands = sorted({v for vs in list(own.values()) + list(others.values()) for v in vs if v is not None} | {current})
    best = None
    for t in cands:
        e = evaluate(op, t, own, others)
        worst = max((r for r in e["others_pass_by_map"].values() if r is not None), default=0.0)
        if worst > MAX_OTHERS:
            continue
        key = (e["bot_pass"], -worst, -abs(t - current))
        if best is None or key > best[0]:
            best = (key, t)
    if best is None:
        return None
    values = [v for vs in list(own.values()) + list(others.values()) for v in vs if v is not None]
    return evaluate(op, roundest(op, best[1], values), own, others)


def separates(e):
    if e is None:
        return False
    for m, b in e["bot_pass_by_map"].items():
        o = e["others_pass_by_map"][m]
        if o is None or o > MAX_OTHERS or b is None or b <= o:
            return False
    return True


def run(bundles, sheets):
    groups = by_map(bundles)
    out = []
    for sh in sheets:
        for item in sh.get("signature") or []:
            if not item.get("gate", True):
                continue
            own, others = {}, {}
            for key, Bs in groups.items():
                own[key[0]] = [value(B, next(s for s in B.slots if B.code[s] == sh["code"]), item["metric"], sh) for B in Bs]
                others[key[0]] = roster_values(Bs, sh["code"], item["metric"], sh)
            row = {"bot": sh["code"], "metric": item["metric"], "op": item["op"], "current": item["value"],
                   "at_current": evaluate(item["op"], item["value"], own, others)}
            if item["op"] == "==":
                row["fitted"], row["note"] = None, "equality: reported, not fitted"
                row["separates"] = separates(row["at_current"])
            else:
                row["fitted"] = fit(item["op"], item["value"], own, others)
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
        line = (f"  {r['bot']} {r['metric']} {r['op']}  current {r['current']}: bot {c['passes']}/{c['matches']}, "
                f"others {', '.join(f'{m} {pct(v)}' for m, v in c['others_pass_by_map'].items())}")
        if f is not None:
            line += (f"  ->  fitted {f['threshold']}: bot {f['passes']}/{f['matches']} "
                     f"({', '.join(f'{m} {pct(v)}' for m, v in f['bot_pass_by_map'].items())}), "
                     f"others {', '.join(f'{m} {pct(v)}' for m, v in f['others_pass_by_map'].items())}")
        line += "  SEPARATES" if r["separates"] else f"  does not separate ({r.get('note', '')})"
        print(line)


def write(rows, sheets, run_name, source):
    """Replace `value` of each separating fitted item in its sheet, with a comment naming the run."""
    files = {sh["code"]: os.path.join(roster.ROOT, sh["_file"]) for sh in sheets}
    for r in rows:
        if r["fitted"] is None or not r["separates"]:
            continue
        path = files[r["bot"]]
        with open(path) as f:
            text = f.read()
        pat = re.compile(r"(- metric: " + re.escape(r["metric"]) + r"\b[^\n]*\n(?:[ \t]+[^\n]*\n)*?[ \t]+value: )[^\n]*\n")
        t = r["fitted"]["threshold"]
        new = (f"\\g<1>{t}   # {run_name}: fitted on {source} (tools/fit_thresholds.py); "
               f"was {r['current']}\n")
        text, n = pat.subn(new, text, count=1)
        if n != 1:
            raise SystemExit(f"{path}: no `value` line for {r['metric']}")
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {r['bot']} {r['metric']} {r['op']} {t} to {os.path.relpath(path, roster.ROOT)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--write", action="store_true", help="write separating fitted thresholds into the sheets")
    ap.add_argument("--run", default="fit-2d", help="name of the fit run, for the sheet comments")
    ap.add_argument("--source", default="eval-2c", help="name of the evaluation fitted on, for the sheet comments")
    args = ap.parse_args(argv)
    sheets = roster.load()
    rows = run([Bundle(p) for p in args.bundles], sheets)
    show(rows)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"run": args.run, "source": args.source, "max_others": MAX_OTHERS, "fits": rows}, f, indent=1)
    if args.write:
        write(rows, sheets, args.run, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
