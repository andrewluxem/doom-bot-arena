"""Smoke bench: 8 headless placeholder bots to tic 300.

    python -m engine.bench.smoke --seed 1234 --out out/bench

Every process logs the full PLAYERn_FRAGCOUNT vector each tic; they must agree
at every matching tic. Also checks lockstep (no gaps), the slot to
PLAYER_NUMBER mapping, and event reconciliation.
"""

import argparse
import datetime as dt
import os
import sys

from engine import bundle, match, roster
from engine.bench.controlled import report
from engine.runner import build, preflight
from tools import extract_map

TICS = 300


def main(argv=None):
    ap = argparse.ArgumentParser(description="Smoke bench: 8 headless players to tic 300.")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--port", type=int, default=5032)
    ap.add_argument("--out", default="out/bench")
    args = ap.parse_args(argv)
    err = preflight()
    if err:
        print(f"bench: refusing to run: {err}", file=sys.stderr)
        return 2
    sheets = roster.load()
    workdir = os.path.join(args.out, f"smoke-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-s{args.seed}")
    print(f"smoke bench: {workdir}")
    try:
        capture = match.run_match(sheets=sheets, iwad=extract_map.default_iwad(), map_name="MAP01", seed=args.seed,
                                  duration_tics=TICS, port=args.port, respawn_delay=2, workdir=workdir,
                                  video=False, log=lambda m: None)
    except match.MatchFailed as e:
        report("all 8 processes reach tic 300", [str(e)])
        return 1
    data, problems, _ = build(capture, TICS, sheets)
    _, _, fragvecs = bundle.read_capture(workdir)
    compared = len({v[0] for vecs in fragvecs.values() for v in vecs})
    ok = report(f"frag-count vectors agree across 8 processes at {compared} tics",
                [f"mismatch at tics {data['mismatches'][:10]}"] if data["mismatches"] else [])
    ok &= report(f"lockstep, mapping {data['numbers']}, events reconcile", problems)
    final = fragvecs[1][-1][1:]
    print(f"      final frag vector (by player number): {final}; events: "
          f"{ {t: sum(1 for e in data['events'] if e['type'] == t) for t in ('frag', 'telefrag', 'suicide', 'respawn')} }")
    print("SMOKE BENCH " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
