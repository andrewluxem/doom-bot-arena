"""Spot scoring for stationary styles, from map.json with 2D raycasts against linedefs.

Two modes (characters/README.md `spots.mode`):

  sightlines_max               a perch: the longest sightlines, clipped at the far end of
                               the bot's preferred_range
  sightlines_min_near_traffic  an ambush: little exposure, but sightlines that cover
                               routes within ROUTE_RADIUS, off the routes themselves.
                               score = sqrt(covered routes) * (1 - exposure)^2 * sqrt(1 - own traffic);
                               with a plain product the open central room won, since coverage
                               grows with exposure

Sight is 2.5D: a ray from eye height (floor + 41) stops at a one-sided line, a closed
door, or a two-sided line whose far sector's floor is above, or ceiling below, eye height.

Routes are the nav-graph shortest paths from every deathmatch start to every weapon and timed
item: where players go after they spawn. A route is covered by a spot when one of its nodes
lies within ROUTE_RADIUS of the spot and in its sight; own traffic is how many routes pass
through the spot's node, normalised to the busiest node. (Spots version 1 scored covered
traffic, the visit counts of shortest paths between every pair of starts, weapons and items,
seen inside the bot's preferred_range. On MAP01 that put NUL's holds in sight of an enemy only
0.16 of the time; tools/hold_contact.py.) Everything is cached per map and parameters.
"""

import json
import math
import os

import numpy as np

from engine import items as itemfacts
from engine.geometry import EYE_HEIGHT
from engine.personality import nav

VERSION = 2
ROUTE_RADIUS = 256.0
RAYS = 72
RAY_MAX = 2048.0
CANDIDATE_GRID = 64  # score nav grid nodes on a coarser lattice
TOP = 40
MIN_SPACING = 128
MODES = ("sightlines_max", "sightlines_min_near_traffic")


class Sight:
    def __init__(self, map_data):
        L = map_data["linedefs"]
        S = map_data["sectors"]
        self.ax = np.array([l["x1"] for l in L], float)
        self.ay = np.array([l["y1"] for l in L], float)
        self.ex = np.array([l["x2"] - l["x1"] for l in L], float)
        self.ey = np.array([l["y2"] - l["y1"] for l in L], float)
        self.front = [l["front_sector"] for l in L]
        self.back = [l["back_sector"] for l in L]
        self.two_sided = [l["two_sided"] for l in L]
        self.floor = [s["floor"] for s in S]
        self.ceil = [s["ceiling"] for s in S]
        ang = np.arange(RAYS) * (2 * math.pi / RAYS)
        self.dx, self.dy = np.cos(ang), np.sin(ang)

    def rays(self, x, y, sector):
        """Sight length along RAYS evenly spaced directions from (x, y) at eye height in `sector`."""
        eye = self.floor[sector] + EYE_HEIGHT
        dx, dy = self.dx[:, None], self.dy[:, None]
        den = dx * self.ey[None, :] - dy * self.ex[None, :]
        wx, wy = (self.ax - x)[None, :], (self.ay - y)[None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (wx * self.ey[None, :] - wy * self.ex[None, :]) / den
            u = (wx * dy - wy * dx) / den
        valid = (np.abs(den) > 1e-9) & (s > 1e-6) & (u >= 0) & (u <= 1) & (s < RAY_MAX)
        # origin side of each line: front is to the right of v1 -> v2
        origin_front = (self.ex * (y - self.ay) - self.ey * (x - self.ax)) < 0
        out = np.full(RAYS, RAY_MAX)
        for r in range(RAYS):
            idx = np.nonzero(valid[r])[0]
            for i in idx[np.argsort(s[r, idx])]:
                if not self.two_sided[i]:
                    out[r] = s[r, i]
                    break
                far = self.back[i] if origin_front[i] else self.front[i]
                if far is None or self.ceil[far] - self.floor[far] <= 0 or self.floor[far] > eye or self.ceil[far] < eye:
                    out[r] = s[r, i]
                    break
        return out


def routes(graph):
    """Node lists of the shortest paths from every deathmatch start to every weapon and timed item."""
    m = graph.geo.m
    near = lambda x, y: graph.nearest(x, y, max_dist=128, need_clear=False)
    starts = sorted({near(s["x"], s["y"]) for s in m["deathmatch_starts"]} - {None})
    goals = sorted({near(w["x"], w["y"]) for w in m["weapons"] if w["in_deathmatch"]}
                   | {near(i["x"], i["y"]) for i in m.get("items", []) if i["in_deathmatch"] and i["name"] in itemfacts.TIMED}
                   - {None})
    out = []
    for a in starts:
        _, prev = graph.dijkstra(a)
        for b in goals:
            if b == a or b not in prev:
                continue
            path, u = [], b
            while u is not None:
                path.append(u)
                u = prev[u]
            out.append(path[::-1])
    return out


def _watch(rays, weights=None, half=6):
    """Centre angle (degrees) of the 2*half+1 ray window with the largest summed weight."""
    w = rays if weights is None else weights
    best, best_i = -1.0, 0
    for i in range(RAYS):
        tot = sum(w[(i + k) % RAYS] for k in range(-half, half + 1))
        if tot > best:
            best, best_i = tot, i
    return best_i * 360.0 / RAYS


def _spread(cands):
    """Best first, dropping candidates closer than MIN_SPACING to a better one."""
    out = []
    for c in sorted(cands, key=lambda c: -c["score"]):
        if all(math.hypot(c["x"] - o["x"], c["y"] - o["y"]) >= MIN_SPACING for o in out):
            out.append(c)
        if len(out) == TOP:
            break
    return out


def compute(map_data, graph, requests):
    """requests: [(mode, rmin, rmax)] -> {"params": [...], "candidates": {mode: [...]}}"""
    sight = Sight(map_data)
    lattice = [i for i, (x, y, s) in enumerate(graph.nodes)
               if (x - graph.geo.bounds["min_x"] - nav.GRID // 2) % CANDIDATE_GRID == 0
               and (y - graph.geo.bounds["min_y"] - nav.GRID // 2) % CANDIDATE_GRID == 0
               and graph.clearance(x, y) >= 24]
    rays = {i: sight.rays(graph.nodes[i][0], graph.nodes[i][1], graph.nodes[i][2]) for i in lattice}
    paths = routes(graph) if any(m == "sightlines_min_near_traffic" for m, _, _ in requests) else []
    on_route = {}  # node -> the routes through it
    for k, path in enumerate(paths):
        for n in path:
            on_route.setdefault(n, set()).add(k)
    busiest = max((len(r) for r in on_route.values()), default=1)
    route_nodes = np.array(sorted(on_route), dtype=int)
    route_xy = np.array([graph.nodes[n][:2] for n in route_nodes], float).reshape(-1, 2)
    out = {"params": [], "candidates": {}}
    for mode, rmin, rmax in requests:
        if mode in out["candidates"]:
            continue
        param = {"mode": mode, "range": [rmin, rmax], "rays": RAYS, "ray_max": RAY_MAX,
                 "lattice": CANDIDATE_GRID, "candidates_scored": len(lattice)}
        if mode == "sightlines_min_near_traffic":
            param.update({"traffic": "routes", "routes": len(paths), "route_radius": ROUTE_RADIUS})
        out["params"].append(param)
        cands = []
        for i in lattice:
            x, y, s = graph.nodes[i]
            r = rays[i]
            if mode == "sightlines_max":
                score = float(np.minimum(r, rmax).mean() / rmax)
                watch = _watch(np.minimum(r, rmax))
            else:
                exposure = float(np.minimum(r, 1024).mean() / 1024)
                weights = np.zeros(RAYS)
                covered = set()
                d = np.hypot(route_xy[:, 0] - x, route_xy[:, 1] - y) if len(route_xy) else np.zeros(0)
                for j in np.nonzero(d <= ROUTE_RADIUS)[0]:
                    n = int(route_nodes[j])
                    tx, ty, _ = graph.nodes[n]
                    k = int(round(math.degrees(math.atan2(ty - y, tx - x)) / (360.0 / RAYS))) % RAYS
                    if n == i or r[k] >= d[j] - 16:
                        weights[k] += len(on_route[n])
                        covered |= on_route[n]
                own = len(on_route.get(i, ())) / busiest
                score = math.sqrt(len(covered)) * (1 - exposure) ** 2 * math.sqrt(max(0.0, 1 - own))
                watch = _watch(r, weights)
            cands.append({"id": i, "x": x, "y": y, "sector": s, "floor": graph.floor[s],
                          "score": round(score, 5), "watch_angle": round(watch, 1)})
        top = max((c["score"] for c in cands), default=1.0) or 1.0
        for c in cands:
            c["score"] = round(c["score"] / top, 4)
        out["candidates"][mode] = _spread(cands)
    return out


def requests_for(sheets):
    return sorted({(sh["spots"]["mode"], *sh["temperament"]["preferred_range"])
                   for sh in sheets if sh.get("spots")})


def table(map_data, cache_dir, requests, graph=None):
    """Spot candidates for `requests`, cached by IWAD sha256, map, VERSION and the requests."""
    key = "-".join(f"{m[:6]}{a}_{b}" for m, a, b in requests) or "none"
    # candidate ids are nav node indices, so the nav graph version is part of the key
    path = nav.cache_path(cache_dir, map_data, f"spots-{key}-nav{nav.VERSION}", VERSION).replace(".pickle", ".json")
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    graph = graph or nav.load(map_data, cache_dir)
    t = compute(map_data, graph, requests) if requests else {"params": [], "candidates": {}}
    os.makedirs(cache_dir, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(t, f)
    os.replace(tmp, path)
    return t
