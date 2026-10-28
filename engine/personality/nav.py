"""Navigation graph for one map, built from map.json geometry, searched with A*.

Nodes are a 32-unit grid of standable points plus two portal nodes per traversable
two-sided linedef (one on each side), so narrow doorways are always represented.
Edges are directed (a ledge can be dropped but not climbed) and carry a kind:

  walk         plain movement
  door         crosses a manual door line; the bot presses USE facing it on approach
  remote_door  enters a sector opened from elsewhere; passable only while it is open
  lift         steps on or off a lift sector; info is the lift sector. Heights are
               checked live while following (wait for the platform, USE its switch)
  teleport     walks into a teleporter line from its front side and arrives at the
               destination node; info is the line id

Exit lines (map.json `exit`) are walls: crossing or using one kills the player. Solid things
(map.json `obstacles`: trees, columns, lamps, barrels) are discs nodes and edges keep clear of.
Freedoom 0.13 MAP01's north third is reachable only by its fast lift or its two
teleporters, and one pocket (a deathmatch start and the plasma rifle) only by the lift.
"""

import heapq
import math
import os
import pickle

from engine.geometry import (PLAYER_HEIGHT, STEP_HEIGHT, Grid, MapGeometry, dist_point_segment, orient,
                             segments_cross)

VERSION = 4
GRID = 32
RADIUS = 16  # player radius
NODE_CLEARANCE = 17
PORTAL_OFFSET = 24
LINK_RADIUS = 64
DOOR_COST = 48  # extra cost of a door edge (stop, use, wait)

MANUAL_DOORS = {1, 26, 27, 28, 31, 32, 33, 34, 117, 118}
REMOTE_DOORS = {2, 4, 29, 46, 61, 63, 86, 90, 103, 105, 106, 108, 109, 111, 112, 114, 115, 133, 134, 135, 136, 137}
LIFTS = {10, 21, 62, 88, 120, 121, 122, 123}
TELEPORTS = {39, 97, 125, 126}
USABLE_KINDS = {"walk", "door", "remote_door", "teleport", "lift"}
LIFT_COST = 160  # the platform takes about 3 s to come round
TELEPORT_APPROACH = 72


def segment_hits_box(p, q, cx, cy, half):
    """Does segment pq enter the open axis-aligned square of half-size `half` centred on (cx, cy)?
    Doom collides boxes, not circles: a player (half-size 16) touches a thing of radius r when both
    |dx| and |dy| are below r + 16. Liang-Barsky clipping against the grown square."""
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for d, lo, hi in ((dx, cx - half - p[0], cx + half - p[0]), (dy, cy - half - p[1], cy + half - p[1])):
        if abs(d) < 1e-12:
            if not (lo < 0 < hi):
                return False
            continue
        a, b = lo / d, hi / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 >= t1:
            return False
    return True


def seg_seg_dist(p, q, a, b):
    if segments_cross(p, q, a, b):
        return 0.0
    return min(dist_point_segment(p, a, b), dist_point_segment(q, a, b),
               dist_point_segment(a, p, q), dist_point_segment(b, p, q))


class Line:
    __slots__ = ("id", "a", "b", "front", "back", "special", "tag", "solid", "teleport")

    def __init__(self, l):
        self.id = l["id"]
        self.a, self.b = (l["x1"], l["y1"]), (l["x2"], l["y2"])
        self.front, self.back = l["front_sector"], l["back_sector"]
        self.special, self.tag = l["special"], l["tag"]
        self.teleport = l["special"] in TELEPORTS and l["two_sided"]
        self.solid = (not l["two_sided"]) or l["impassable"] or l.get("exit", False)


class NavGraph:
    def __init__(self, map_data):
        self.geo = MapGeometry(map_data)
        sectors = map_data["sectors"]
        self.floor = [s["floor"] for s in sectors]
        self.ceil = [s["ceiling"] for s in sectors]
        self.lines = [Line(l) for l in map_data["linedefs"]]
        self.line_grid = Grid(map_data["bounds"], 128)
        for ln in self.lines:
            self.line_grid.add_box(ln, ln.a[0], ln.a[1], ln.b[0], ln.b[1])
        tags = lambda specials: {ln.tag for ln in self.lines if ln.special in specials and ln.tag}
        self.lift_sectors = {i for i, s in enumerate(sectors) if s["tag"] and s["tag"] in tags(LIFTS)}
        self.remote_doors = {i for i, s in enumerate(sectors) if s["tag"] and s["tag"] in tags(REMOTE_DOORS)}
        self.manual_doors = {ln.back for ln in self.lines if ln.special in MANUAL_DOORS and ln.back is not None}
        self.door_lines = {ln.id for ln in self.lines if ln.special in MANUAL_DOORS}
        self.lift_switch_lines = {ln.id for ln in self.lines if ln.special in LIFTS and ln.special in (21, 62, 122, 123)}
        self.door_open_ceiling = {s: self._door_open_ceiling(s) for s in self.manual_doors | self.remote_doors}
        self.m_teleport_dests = map_data.get("teleport_destinations", [])
        self.obstacles = [(o["x"], o["y"], o["radius"]) for o in map_data.get("obstacles", []) if o["in_deathmatch"]]
        self.obstacle_grid = Grid(map_data["bounds"], 128)
        for ob in self.obstacles:
            self.obstacle_grid.add_box(ob, ob[0] - ob[2], ob[1] - ob[2], ob[0] + ob[2], ob[1] + ob[2])
        self.nodes = []  # (x, y, sector)
        self.edges = []  # per node: [(to, cost, kind, info)]
        self._build()

    # ------------------------------------------------------------ geometry
    def _door_open_ceiling(self, s):
        """A Doom door opens to its lowest neighbouring ceiling minus 4."""
        neigh = set()
        for ln in self.lines:
            if s in (ln.front, ln.back) and ln.front is not None and ln.back is not None:
                neigh.add(ln.back if ln.front == s else ln.front)
        neigh.discard(s)
        return (min(self.ceil[n] for n in neigh) - 4) if neigh else self.ceil[s]

    def lines_near(self, x0, y0, x1, y1, pad=RADIUS):
        return self.line_grid.query_box(x0 - pad, y0 - pad, x1 + pad, y1 + pad)

    def clearance(self, x, y, limit=64):
        """Distance to the nearest wall or solid thing's edge, up to limit."""
        best = limit
        for ln in self.lines_near(x, y, x, y, limit):
            if ln.solid:
                best = min(best, dist_point_segment((x, y), ln.a, ln.b))
        for ox, oy, r in self.obstacle_grid.query_box(x - limit - 32, y - limit - 32, x + limit + 32, y + limit + 32):
            best = min(best, max(0.0, max(abs(ox - x), abs(oy - y)) - r))  # box distance
        return best

    def standable(self, s, live=None):
        if s is None:
            return False
        ceil = self.door_open_ceiling.get(s, self.ceil[s])
        return ceil - self.floor[s] >= PLAYER_HEIGHT

    def move_check(self, p, q, live=None, sp=None, sq=None):
        """(kind, info) for walking p -> q, or None if blocked. info names the door line or sector.

        A two-sided line counts as crossed when p and q are on opposite sides of it and the segment
        reaches it, endpoints included, so a path through a shared vertex is checked too. As a last
        check the step between the sectors at p and q (sp, sq if known) must be climbable.
        """
        x0, y0, x1, y1 = min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])
        for ox, oy, r in self.obstacle_grid.query_box(x0 - RADIUS - 32, y0 - RADIUS - 32, x1 + RADIUS + 32, y1 + RADIUS + 32):
            if segment_hits_box(p, q, ox, oy, r + RADIUS - 1):
                return None
        crossings = []
        for ln in self.lines_near(x0, y0, x1, y1):
            if ln.solid:
                if seg_seg_dist(p, q, ln.a, ln.b) < RADIUS - 1:
                    return None
                continue
            d1, d2 = orient(ln.a, ln.b, p), orient(ln.a, ln.b, q)
            if d1 * d2 < 0 and orient(p, q, ln.a) * orient(p, q, ln.b) <= 0:
                if ln.teleport and d1 < 0:
                    return None  # entering from the front teleports: only teleport edges do that
                # parameter along p->q, for ordering the crossings
                d = (q[0] - p[0], q[1] - p[1])
                e = (ln.b[0] - ln.a[0], ln.b[1] - ln.a[1])
                den = d[0] * e[1] - d[1] * e[0]
                t = ((ln.a[0] - p[0]) * e[1] - (ln.a[1] - p[1]) * e[0]) / den
                crossings.append((t, ln))
        crossings.sort(key=lambda c: c[0])
        kind, info = "walk", None
        for _, ln in crossings:
            on_front = orient(ln.a, ln.b, p) < 0  # front is to the right of v1 -> v2
            s_from, s_to = (ln.front, ln.back) if on_front else (ln.back, ln.front)
            if s_from in self.lift_sectors or s_to in self.lift_sectors:
                return "lift", s_to if s_to in self.lift_sectors else s_from
            c_from = self._ceiling(s_from, live)
            c_to = self._ceiling(s_to, live)
            f_from, f_to = self.floor[s_from], self.floor[s_to]
            if f_to - f_from > STEP_HEIGHT or min(c_from, c_to) - max(f_from, f_to) < PLAYER_HEIGHT:
                return None
            if ln.id in self.door_lines and kind == "walk":
                kind, info = "door", ln.id
            elif s_to in self.remote_doors and kind == "walk":
                kind, info = "remote_door", s_to
        sp = self.geo.sector_at(*p) if sp is None else sp
        sq = self.geo.sector_at(*q) if sq is None else sq
        if sp is not None and sq is not None and not (sp in self.lift_sectors or sq in self.lift_sectors):
            if self.floor[sq] - self.floor[sp] > STEP_HEIGHT:
                return None
        return kind, info

    def _ceiling(self, s, live):
        if live is not None and s in live:
            return live[s]
        return self.door_open_ceiling.get(s, self.ceil[s])

    # ------------------------------------------------------------ build
    def _add_node(self, x, y, clearance):
        s = self.geo.sector_at(x, y)
        if not self.standable(s) or self.clearance(x, y) < clearance:
            return
        if any(dist_point_segment((x, y), ln.a, ln.b) < 2.0 for ln in self.lines_near(x, y, x, y, 4)):
            return  # on a line, its sector is ambiguous
        self.nodes.append((x, y, s))

    def _build(self):
        b = self.geo.bounds
        x = b["min_x"] + GRID // 2
        while x < b["max_x"]:
            y = b["min_y"] + GRID // 2
            while y < b["max_y"]:
                self._add_node(float(x), float(y), NODE_CLEARANCE)
                y += GRID
            x += GRID
        for d in self.m_teleport_dests:
            self._add_node(float(d["x"]), float(d["y"]), RADIUS - 1)
        for ln in self.lines:
            if ln.solid or ln.teleport:
                continue
            dx, dy = ln.b[0] - ln.a[0], ln.b[1] - ln.a[1]
            L = math.hypot(dx, dy)
            if L < 2 * RADIUS + 2:
                continue
            nx, ny = dy / L, -dx / L
            mx, my = (ln.a[0] + ln.b[0]) / 2, (ln.a[1] + ln.b[1]) / 2
            for sgn in (1, -1):
                self._add_node(mx + sgn * PORTAL_OFFSET * nx, my + sgn * PORTAL_OFFSET * ny, RADIUS - 1)
        node_grid = Grid(b, LINK_RADIUS)
        for i, (nx_, ny_, _) in enumerate(self.nodes):
            node_grid.add_box(i, nx_, ny_, nx_, ny_)
        self.node_grid = node_grid
        self.edges = [[] for _ in self.nodes]
        for i, (xi, yi, _) in enumerate(self.nodes):
            for j in node_grid.query_box(xi - LINK_RADIUS, yi - LINK_RADIUS, xi + LINK_RADIUS, yi + LINK_RADIUS):
                if j == i:
                    continue
                xj, yj, _ = self.nodes[j]
                d = math.hypot(xj - xi, yj - yi)
                if d > LINK_RADIUS or d < 1:
                    continue
                res = self.move_check((xi, yi), (xj, yj), sp=self.nodes[i][2], sq=self.nodes[j][2])
                if res is None:
                    continue
                kind, info = res
                extra = DOOR_COST if kind == "door" else LIFT_COST if kind == "lift" else 0
                self.edges[i].append((j, d + extra, kind, info))
        self._add_teleport_edges()
        self._keep_main_component(b)

    def _add_teleport_edges(self):
        """Front-side approach nodes of each teleporter line -> the node at its destination thing."""
        self.teleport_lines = {}
        for ln in self.lines:
            if not ln.teleport:
                continue
            dest = None
            for d in self.m_teleport_dests:
                s = self.geo.sector_at(d["x"], d["y"])
                if s is not None and ln.tag and self.geo.sectors[s]["tag"] == ln.tag:
                    dest = d
            if dest is None:
                continue
            target = min(range(len(self.nodes)), key=lambda i: math.hypot(self.nodes[i][0] - dest["x"],
                                                                           self.nodes[i][1] - dest["y"]))
            dx, dy = ln.b[0] - ln.a[0], ln.b[1] - ln.a[1]
            L = math.hypot(dx, dy)
            nx, ny = dy / L, -dx / L  # unit normal toward the front (right) side
            mid = ((ln.a[0] + ln.b[0]) / 2, (ln.a[1] + ln.b[1]) / 2)
            entry = (mid[0] + 4 * nx, mid[1] + 4 * ny)
            self.teleport_lines[ln.id] = {"mid": mid, "normal": (nx, ny), "dest": (dest["x"], dest["y"]), "node": target}
            for i, (x, y, _) in enumerate(self.nodes):
                d = math.hypot(x - entry[0], y - entry[1])
                if d > TELEPORT_APPROACH or orient(ln.a, ln.b, (x, y)) >= 0:
                    continue
                if self.move_check((x, y), entry) is None:
                    continue
                self.edges[i].append((target, d + 16, "teleport", ln.id))

    def _keep_main_component(self, b):
        """Drop nodes not reachable both ways from the largest strongly connected walk region."""
        n = len(self.nodes)
        fwd = [[j for j, _, k, _ in self.edges[i] if k in USABLE_KINDS] for i in range(n)]
        rev = [[] for _ in range(n)]
        for i in range(n):
            for j in fwd[i]:
                rev[j].append(i)

        def reach(start, adj):
            seen, stack = {start}, [start]
            while stack:
                u = stack.pop()
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            return seen

        best = set()
        tried = set()
        for i in range(n):
            if i in tried or i in best:
                continue
            comp = reach(i, fwd) & reach(i, rev)
            tried |= comp
            if len(comp) > len(best):
                best = comp
        keep = sorted(best)
        remap = {old: new for new, old in enumerate(keep)}
        self.nodes = [self.nodes[i] for i in keep]
        self.edges = [[(remap[j], c, k, info) for j, c, k, info in self.edges[i] if j in remap] for i in keep]
        self.node_grid = Grid(b, LINK_RADIUS)
        for i, (x, y, _) in enumerate(self.nodes):
            self.node_grid.add_box(i, x, y, x, y)

    # ------------------------------------------------------------ queries
    def nearest(self, x, y, max_dist=192, need_clear=True):
        """Nearest node reachable in a straight line from (x, y)."""
        cands = []
        for r in (64, 128, max_dist):
            cands = [(math.hypot(self.nodes[i][0] - x, self.nodes[i][1] - y), i)
                     for i in self.node_grid.query_box(x - r, y - r, x + r, y + r)]
            if cands:
                break
        for d, i in sorted(cands):
            if d > max_dist:
                break
            if not need_clear or self.move_check((x, y), self.nodes[i][:2]) is not None:
                return i
        return min(cands)[1] if cands else None

    def astar(self, start, goal, live=None, allow=USABLE_KINDS, blocked=()):
        """Node path start..goal, or None. remote_door edges need the sector open in `live`;
        `blocked` holds (u, v) edges to skip (where the bot got stuck)."""
        if start is None or goal is None:
            return None
        gx, gy, _ = self.nodes[goal]
        h = lambda i: math.hypot(self.nodes[i][0] - gx, self.nodes[i][1] - gy)
        openq = [(h(start), 0.0, start)]
        came, cost = {start: None}, {start: 0.0}
        while openq:
            _, g, u = heapq.heappop(openq)
            if u == goal:
                path = []
                while u is not None:
                    path.append(u)
                    u = came[u]
                return path[::-1]
            if g > cost[u]:
                continue
            for v, c, kind, info in self.edges[u]:
                if kind not in allow:
                    continue
                if kind == "remote_door" and not self.sector_open(info, live):
                    continue
                if (u, v) in blocked:
                    continue
                ng = g + c
                if ng < cost.get(v, math.inf):
                    cost[v], came[v] = ng, u
                    heapq.heappush(openq, (ng + h(v), ng, v))
        return None

    def sector_open(self, s, live):
        if live is None or s not in live:
            return False
        return live[s] - self.floor[s] >= PLAYER_HEIGHT

    def edge(self, u, v):
        for j, c, kind, info in self.edges[u]:
            if j == v:
                return kind, info
        return None

    def path_length(self, path):
        return sum(math.hypot(self.nodes[a][0] - self.nodes[b][0], self.nodes[a][1] - self.nodes[b][1])
                   for a, b in zip(path, path[1:])) if path else math.inf

    def dijkstra(self, start, allow=USABLE_KINDS, live=None):
        """Path cost and predecessor of every node reachable from start (remote doors only if open in live)."""
        dist, prev = {start: 0.0}, {start: None}
        q = [(0.0, start)]
        while q:
            d, u = heapq.heappop(q)
            if d > dist[u]:
                continue
            for v, c, kind, info in self.edges[u]:
                if kind not in allow or (kind == "remote_door" and not self.sector_open(info, live)):
                    continue
                nd = d + c
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(q, (nd, v))
        return dist, prev

    def kinds(self):
        out = {}
        for es in self.edges:
            for _, _, k, _ in es:
                out[k] = out.get(k, 0) + 1
        return out


def cache_path(cache_dir, map_data, name, version):
    src = map_data["source"]
    return os.path.join(cache_dir, f"{name}-{src['map']}-{src['iwad_sha256'][:12]}-v{version}.pickle")


def load(map_data, cache_dir):
    """The map's graph, built once and cached by IWAD sha256, map and VERSION."""
    path = cache_path(cache_dir, map_data, "nav", VERSION) if cache_dir else None
    if path and os.path.isfile(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    g = NavGraph(map_data)
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(g, f)
        os.replace(tmp, path)
    return g
