"""Does sv_spawnfarthest cause the tic-0 telefrags?

    python -m engine.bench.spawn_telefrags --seeds 1-10 --out out/bench

Hypothesis: at level start every player resolves to the same "farthest"
deathmatch start before anyone else exists, so later spawns telefrag earlier
ones. Two modes per seed, 8 idle players, 40 tics:
  on            +sv_spawnfarthest 1 from the start (the match setting)
  off-then-on   +sv_spawnfarthest 0 at spawn; the host sets it to 1 at tic 1
Counts OB_MPTELEFRAG obituaries at tic 0 and confirms the console change took
effect on every process by querying the CVAR at tic 3.
"""

import argparse
import datetime as dt
import json
import os
import sys

from engine import bundle, match, obituaries, roster
from engine.runner import preflight
from tools import extract_map

TICS = 40


class Idle:
    def __init__(self, slot, sheet, seed, mode):
        self.slot, self.mode = slot, mode

    def act(self, t, state, v):
        cmds = []
        if self.mode == "off-then-on" and t == 1 and self.slot == 1:
            cmds.append("sv_spawnfarthest 1")
        if t == 3:
            cmds.append("sv_spawnfarthest")  # prints the current value into the notifications buffer
        return {}, cmds, "HOLDING_ANGLE", None


def parse_seeds(text):
    if "-" in text:
        a, b = text.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in text.split(",")]


def one_run(sheets, seed, mode, port, out):
    workdir = os.path.join(out, f"{mode}-s{seed}")
    capture = match.run_match(sheets=sheets, iwad=extract_map.default_iwad(), map_name="MAP01", seed=seed,
                              duration_tics=TICS, port=port, respawn_delay=2, workdir=workdir,
                              controller="engine.bench.spawn_telefrags:Idle",
                              controller_kwargs={s: {"mode": mode} for s in roster.SLOTS},
                              video=False, spawnfarthest=(mode == "on"), log=lambda m: None)
    rows, notes, _ = bundle.read_capture(capture["workdir"])
    tic0 = [n["line"] for n in notes[1] if n["tic"] == 0 and n["line"].startswith(obituaries.PREFIX + "OB_MPTELEFRAG|")]
    values = {s: [n["line"] for n in notes[s] if "sv_spawnfarthest" in n["line"]] for s in roster.SLOTS}
    starts = sorted({(r["x"], r["y"]) for slot_rows in rows.values() for r in slot_rows[:1] if r["alive"]})
    return {"seed": seed, "mode": mode, "tic0_telefrags": len(tic0), "lines": tic0,
            "cvar_after": {s: v[-1] if v else None for s, v in values.items()}, "alive_start_positions": starts}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Count tic-0 telefrags with sv_spawnfarthest on vs enabled after tic 1.")
    ap.add_argument("--seeds", default="1-10")
    ap.add_argument("--port", type=int, default=5033)
    ap.add_argument("--out", default="out/bench")
    args = ap.parse_args(argv)
    err = preflight()
    if err:
        print(f"bench: refusing to run: {err}", file=sys.stderr)
        return 2
    sheets = roster.load()
    out = os.path.join(args.out, f"spawn-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}")
    results = []
    for seed in parse_seeds(args.seeds):
        for mode in ("on", "off-then-on"):
            r = one_run(sheets, seed, mode, args.port, out)
            results.append(r)
            cvar = sorted(set(r["cvar_after"].values()), key=str)
            print(f"seed {seed:>3}  {mode:<12} tic-0 telefrags {r['tic0_telefrags']}  "
                  f"distinct alive spawn spots {len(r['alive_start_positions'])}  cvar at tic 3: {cvar}")
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(results, f, indent=1)
    for mode in ("on", "off-then-on"):
        counts = [r["tic0_telefrags"] for r in results if r["mode"] == mode]
        print(f"{mode:<12} runs {len(counts)}  tic-0 telefrags total {sum(counts)}  per run {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
