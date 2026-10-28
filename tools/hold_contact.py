"""Share of a holding bot's hold time with any enemy in view, from match bundles.

    python3 tools/hold_contact.py bundles/eval-2a/2* [--bots NUL,HEX] [--json out.json]

Hold time follows the controller (engine/personality/controller.py): inside each spot the bot
chose (spots.json `chosen`, from_tic to to_tic), it is at its spot from the first tic within
SPOT_ARRIVE of it until it is pushed more than SPOT_LEAVE away. A hold tic is an alive tic of the
displayed match at its spot with `behavior` its sheet's hold label or IN_COMBAT, which is when
the controller holds still.

Bundles do not record what a bot saw, so a contact is reconstructed: another player alive at
that tic, inside the holder's 90-degree view (its `angle`, widened by the player's 16-unit
radius), with Doom's 2.5D line of sight from the holder's eye to any part of the player
(engine.geometry.LineOfSight on map.json heights; doors are as the map data has them, closed).
Heights come from each row's `sector` floor, so this needs schema 2. `target_slot` set during a
hold is reported alongside: the bot names a target only inside its fire window, and keeps the name
0.6 s after the target leaves view. "Either" (contact or a named target) is an upper bound.
"""

import argparse
import json
import math
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import roster  # noqa: E402
from engine.geometry import EYE_HEIGHT, PLAYER_HEIGHT, LineOfSight  # noqa: E402
from engine.personality.controller import SPOT_ARRIVE, SPOT_LEAVE  # noqa: E402

HALF_FOV = 45.0
PLAYER_RADIUS = 16.0


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def hold_tics(rows, chosen, hold_labels, f0, end):
    """Tics at which the bot holds its spot (see module doc)."""
    out = []
    for c in chosen:
        at = False
        for t in range(c["from_tic"], min(c["to_tic"], end - 1) + 1):
            r = rows[t]
            if not r["alive"]:
                at = False
                continue
            d = math.hypot(r["x"] - c["x"], r["y"] - c["y"])
            at = d <= (SPOT_LEAVE if at else SPOT_ARRIVE)
            if at and t >= f0 and r["behavior"] in hold_labels:
                out.append(t)
    return sorted(set(out))


def in_view(sight, floor, me, other):
    dx, dy = other["x"] - me["x"], other["y"] - me["y"]
    d = math.hypot(dx, dy)
    if d < 1.0:
        return True
    off = abs((math.degrees(math.atan2(dy, dx)) - me["angle"] + 180.0) % 360.0 - 180.0)
    if off > HALF_FOV + math.degrees(math.atan2(PLAYER_RADIUS, d)):
        return False
    z0 = floor[me["sector"]] + EYE_HEIGHT
    zb = floor[other["sector"]]
    return sight.clear(me["x"], me["y"], z0, other["x"], other["y"], zb, zb + PLAYER_HEIGHT)


def measure(path, code, sheet):
    with open(os.path.join(path, "match.json")) as f:
        m = json.load(f)
    with open(os.path.join(path, "map.json")) as f:
        map_data = json.load(f)
    with open(os.path.join(path, "spots.json")) as f:
        spots = json.load(f)
    f0, end = m["frame0_tic"], m["frame0_tic"] + m["duration_tics"]
    slot = next(b["slot"] for b in m["bots"] if b["code"] == code)
    rows = {}
    for r in read_jsonl(os.path.join(path, "tics.jsonl")):
        rows.setdefault(r["slot"], [None] * end)[r["tic"]] = r
    chosen = [c for c in spots["chosen"] if c["slot"] == slot]
    labels = {sheet["spots"]["hold_label"], "IN_COMBAT"}
    held = hold_tics(rows[slot], chosen, labels, f0, end)
    sight = LineOfSight(map_data)
    floor = [s["floor"] for s in map_data["sectors"]]
    contact = named = either = 0
    for t in held:
        me = rows[slot][t]
        others = [rows[s][t] for s in rows if s != slot and rows[s][t]["alive"]]
        seen = any(in_view(sight, floor, me, o) for o in others)
        contact += seen
        named += me["target_slot"] is not None
        either += seen or me["target_slot"] is not None
    alive = sum(1 for t in range(f0, end) if rows[slot][t]["alive"])
    return {"bundle": os.path.basename(os.path.normpath(path)), "map": m["map"], "seed": m["seed"], "code": code,
            "alive_tics": alive, "hold_tics": len(held), "contact_tics": contact, "named_tics": named, "either_tics": either,
            "hold_share_of_alive": round(len(held) / alive, 4) if alive else None,
            "contact_share": round(contact / len(held), 4) if held else None,
            "named_share": round(named / len(held), 4) if held else None,
            "either_share": round(either / len(held), 4) if held else None,
            "spots": len(chosen)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="+")
    ap.add_argument("--bots", default="NUL")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    sheets = {sh["code"]: sh for sh in roster.load()}
    out = []
    for code in [c.strip().upper() for c in args.bots.split(",")]:
        res = [measure(p, code, sheets[code]) for p in args.bundles]
        held = sum(r["hold_tics"] for r in res)
        contact = sum(r["contact_tics"] for r in res)
        named = sum(r["named_tics"] for r in res)
        either = sum(r["either_tics"] for r in res)
        share = lambda k: k / held if held else float("nan")
        print(f"{code}: hold time with any contact in view {contact}/{held} tics = {share(contact):.3f}; "
              f"with a named target {share(named):.3f}; either (upper bound) {share(either):.3f}")
        for r in res:
            print(f"    seed {r['seed']} {r['map']}: hold {r['hold_tics']} tics ({r['hold_share_of_alive']} of alive), "
                  f"contact {r['contact_share']}, named {r['named_share']}, spots chosen {r['spots']}")
        out.append({"code": code, "hold_tics": held, "contact_tics": contact, "named_tics": named, "either_tics": either,
                    "contact_share": round(contact / held, 4) if held else None, "matches": res})
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
