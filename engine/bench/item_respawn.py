"""Item respawn probe: does a picked-up item come back under the match's host arguments?

    python -m engine.bench.item_respawn --map MAP10 --out out/bench

One player hosts a one-player deathmatch with the match's host arguments, plus
cheats so the script can `warp` and `take`. For each configuration (the match's
host arguments, and a negative control with item respawn turned off) and each
probed item kind, the player drops to 50 health and 0 armor, warps onto
the item and steps across it (a warp alone touches nothing), and warps back to
its spawn. Then, once a second for 40 s, it resets to 50 health and no armor and
crosses the item again. An item that respawns (30 s after its pickup in ZDoom)
raises health or armor again; one that does not never does.

Objects info cannot tell: ZDoom hides a picked-up item rather than removing it.
"""

import argparse
import json
import os
import sys

from engine import game
from tools import extract_map

WATCH_TICS = 40 * game.TICRATE
PROBE = {"MAP10": ("Medikit", "GreenArmor", "Megasphere"), "E1M5": ("Medikit", "GreenArmor")}


def probe(iwad, map_name, item, extra_args, seed, port, commands=()):
    m = extract_map.extract(iwad, map_name)
    target = next(i for i in m["items"] if i["name"] == item)
    args = game.host_args(1, port, 2, cheats=True) + extra_args
    g = game.make_game(iwad=iwad, map_name=map_name, seed=seed, args=args, wads=[])
    g.init()
    try:
        def step(n=1, action=None):
            for _ in range(n):
                g.make_action(game.action_vector(action or {}), 1)
            st = g.get_state()
            return st, dict(zip(game.VARIABLES, st.game_variables))

        for c in commands:  # console settings applied after init (the negative control)
            g.send_game_command(c)
        st, v = step(5)
        home = (v["POSITION_X"], v["POSITION_Y"])

        def cross():
            """Drop to 50 health and no armor, step across the item, come home; returns (before, after)."""
            st, v0 = step(1)
            if v0["HEALTH"] > 50:
                g.send_game_command("take health %d" % (v0["HEALTH"] - 50))
            g.send_game_command("take armor")
            st, v0 = step(3)
            g.send_game_command("warp %d %d" % (target["x"], target["y"]))
            step(2)
            step(2, {"MOVE_FORWARD": 1})  # a warp does not touch things; moving across the item does
            st, v1 = step(2, {"MOVE_BACKWARD": 1})
            g.send_game_command("warp %d %d" % home)
            step(2)
            return (int(v0["HEALTH"]), int(v0["ARMOR"])), (int(v1["HEALTH"]), int(v1["ARMOR"]))

        first = cross()
        picked_tic = g.get_episode_time()
        back_at, attempts = None, 0
        while g.get_episode_time() - picked_tic < WATCH_TICS:
            step(35 - 13)  # one crossing a second; a crossing takes 13 tics
            attempts += 1
            before, after = cross()
            if after != before:
                back_at = g.get_episode_time() - picked_tic
                break
        return {"item": item, "at": [target["x"], target["y"]], "extra_args": extra_args, "commands": list(commands),
                "first_pickup": {"before": first[0], "after": first[1]}, "recrossings": attempts,
                "picked_up_again_after_tics": back_at}
    finally:
        g.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--map", default="MAP10")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--port", type=int, default=5129)
    p.add_argument("--out", default="out/bench")
    args = p.parse_args(argv)
    iwad = extract_map.default_iwad(args.map)
    # The match's host arguments, and a negative control that turns item respawn off after init
    # (on the command line, +sv_itemrespawn 0 is overridden: ViZDoom sets dmflags for -deathmatch).
    configs = {"match host args": (), "control: sv_itemrespawn 0": ("sv_itemrespawn 0",)}
    results = []
    for label, commands in configs.items():
        for item in PROBE.get(args.map.upper(), ("Medikit",)):
            r = probe(iwad, args.map.upper(), item, [], args.seed, args.port, commands)
            r["config"] = label
            results.append(r)
            print(json.dumps(r))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"item_respawn_{args.map.upper()}.json"), "w") as f:
        json.dump(results, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
