"""Observed traffic per map: where players actually were, from match bundles of that map.

Every alive, moving row of every slot in the displayed match counts once in the 64-unit cell holding
its x, y (cells are aligned to the map origin). Moving: more than STILL_RADIUS from where the bot was
STILL_TICS earlier in the same life, as in stationary_share (docs/SIGNATURES.md); the first STILL_TICS
of a life count. Without that, a camper's own hold in an evaluation bundle (NUL and HEX sit for most of
a match) would be the busiest cell on the map and draw it back to the same spot. Only bundles of the same map and IWAD count. Spot
scoring (spots.py) reads a spot's traffic as the sum over its cell and the eight cells around it.

Source (milestone 2D): evaluation bundles, where every slot is a personality, when any of the map
are found; bundles with placeholders (traffic-2c) only when there are none.
"""

import hashlib
import json
import math
import os
from collections import Counter

VERSION = 3  # 2: evaluation bundles first, placeholder bundles as fallback; 3: moving rows only
STILL_TICS = 35
STILL_RADIUS = 32.0
PLACEHOLDER = "engine.bots:PlaceholderBot"
CELL = 64


def cell(x, y):
    return math.floor(x / CELL), math.floor(y / CELL)


def bundle_dirs(roots):
    """Bundle directories under `roots`: each root is a bundle (has match.json) or holds bundles."""
    out = []
    for root in roots:
        if os.path.isfile(os.path.join(root, "match.json")):
            out.append(root)
        elif os.path.isdir(root):
            out += [os.path.join(root, d) for d in sorted(os.listdir(root))
                    if not d.startswith(".") and os.path.isfile(os.path.join(root, d, "match.json"))]
    return out


def build(roots, map_data):
    """The traffic map of map_data's map from the bundles under `roots`, or None if none is of that map."""
    src = map_data["source"]
    found = {"eval": [], "placeholder": []}
    for path in bundle_dirs(roots):
        with open(os.path.join(path, "match.json")) as f:
            m = json.load(f)
        if m.get("map") != src["map"] or m.get("iwad", {}).get("sha256") != src["iwad_sha256"]:
            continue
        evaluation = all(b.get("controller", PLACEHOLDER) != PLACEHOLDER for b in m["bots"])
        found["eval" if evaluation else "placeholder"].append((path, m))
    kind = "eval" if found["eval"] else "placeholder"
    counts, used, samples = Counter(), [], 0
    for path, m in found[kind]:
        f0, end = m["frame0_tic"], m["frame0_tic"] + m["duration_tics"]
        trail = {}  # slot -> positions of the current life, by tic
        with open(os.path.join(path, "tics.jsonl")) as f:
            for line in f:
                r = json.loads(line)
                life = trail.setdefault(r["slot"], {})
                if not r["alive"]:
                    life.clear()
                    continue
                life[r["tic"]] = (r["x"], r["y"])
                life.pop(r["tic"] - STILL_TICS - 1, None)
                back = life.get(r["tic"] - STILL_TICS)
                if back is not None and math.hypot(r["x"] - back[0], r["y"] - back[1]) <= STILL_RADIUS:
                    continue
                if f0 <= r["tic"] < end:
                    counts[cell(r["x"], r["y"])] += 1
                    samples += 1
        used.append(m["id"])
    if not used:
        return None
    return {"traffic_version": VERSION, "cell": CELL, "map": src["map"], "iwad_sha256": src["iwad_sha256"],
            "source": kind, "bundles": used, "samples": samples,
            "cells": {f"{cx},{cy}": n for (cx, cy), n in sorted(counts.items())}}


def digest(traffic):
    """Short content hash, for cache keys."""
    if traffic is None:
        return "routes"
    blob = json.dumps({"v": traffic["traffic_version"], "cells": traffic["cells"]}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


class Grid:
    def __init__(self, traffic):
        self.counts = {tuple(int(v) for v in k.split(",")): n for k, n in traffic["cells"].items()}

    def at(self, x, y):
        return self.counts.get(cell(x, y), 0)

    def around(self, x, y):
        """Traffic in the cell holding (x, y) and the eight cells adjacent to it."""
        cx, cy = cell(x, y)
        return sum(self.counts.get((cx + i, cy + j), 0) for i in (-1, 0, 1) for j in (-1, 0, 1))
