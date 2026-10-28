"""2D map geometry from map.json: sector lookup, segment tests and raycasts.

Shared by the validator (sector cross-check), navigation and spot scoring.
"""

import math

PLAYER_HEIGHT = 56
EYE_HEIGHT = 41
STEP_HEIGHT = 24  # the most a player can step up


def orient(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def segments_cross(p, q, a, b):
    """Proper crossing of segments pq and ab (touching endpoints do not count)."""
    d1, d2 = orient(a, b, p), orient(a, b, q)
    d3, d4 = orient(p, q, a), orient(p, q, b)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def dist_point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def ray_hit(o, d, a, b):
    """Distance along unit ray o + s*d to segment ab, or None."""
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = d[0] * ey - d[1] * ex
    if abs(den) < 1e-12:
        return None
    wx, wy = a[0] - o[0], a[1] - o[1]
    s = (wx * ey - wy * ex) / den
    u = (wx * d[1] - wy * d[0]) / den
    if s > 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return s
    return None


class Grid:
    """Uniform bucket index over segments (or boxes) for fast local queries."""

    def __init__(self, bounds, cell=128):
        self.cell = cell
        self.x0, self.y0 = bounds["min_x"] - cell, bounds["min_y"] - cell
        self.nx = int((bounds["max_x"] - self.x0) // cell) + 2
        self.ny = int((bounds["max_y"] - self.y0) // cell) + 2
        self.buckets = {}

    def _range(self, x0, y0, x1, y1):
        i0 = max(0, int((min(x0, x1) - self.x0) // self.cell))
        i1 = min(self.nx - 1, int((max(x0, x1) - self.x0) // self.cell))
        j0 = max(0, int((min(y0, y1) - self.y0) // self.cell))
        j1 = min(self.ny - 1, int((max(y0, y1) - self.y0) // self.cell))
        return i0, i1, j0, j1

    def add_box(self, item, x0, y0, x1, y1):
        i0, i1, j0, j1 = self._range(x0, y0, x1, y1)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                self.buckets.setdefault((i, j), []).append(item)

    def query_box(self, x0, y0, x1, y1):
        i0, i1, j0, j1 = self._range(x0, y0, x1, y1)
        seen, out = set(), []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                for it in self.buckets.get((i, j), ()):
                    if id(it) not in seen:
                        seen.add(id(it))
                        out.append(it)
        return out

    def query_point(self, x, y):
        i = int((x - self.x0) // self.cell)
        j = int((y - self.y0) // self.cell)
        return self.buckets.get((i, j), ())


class MapGeometry:
    def __init__(self, map_data):
        self.m = map_data
        self.bounds = map_data["bounds"]
        self.sectors = map_data["sectors"]
        self.lines = map_data["linedefs"]
        self._sector_lines = {}
        for l in self.lines:
            f, b = l["front_sector"], l["back_sector"]
            if f == b:
                continue  # a line with the same sector on both sides does not bound it
            for s in (f, b):
                if s is not None:
                    self._sector_lines.setdefault(s, []).append(((l["x1"], l["y1"]), (l["x2"], l["y2"])))
        self._sector_grid = Grid(self.bounds, 64)
        self._sector_box = {}
        for s, segs in self._sector_lines.items():
            xs = [p[0] for seg in segs for p in seg]
            ys = [p[1] for seg in segs for p in seg]
            box = (min(xs), min(ys), max(xs), max(ys))
            self._sector_box[s] = box
            self._sector_grid.add_box(s, *box)

    # ------------------------------------------------------------ sectors
    def _inside(self, s, x, y):
        """Even-odd test against the sector's boundary lines (handles holes and split polygons)."""
        x0, y0, x1, y1 = self._sector_box[s]
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        inside = False
        for (ax, ay), (bx, by) in self._sector_lines[s]:
            if (ay > y) != (by > y):
                cx = ax + (y - ay) * (bx - ax) / (by - ay)
                if cx > x:
                    inside = not inside
        return inside

    def sectors_at(self, x, y):
        return [s for s in self._sector_grid.query_point(x, y) if self._inside(s, x, y)]

    def sector_at(self, x, y):
        """The innermost sector containing (x, y), or None.

        Nested sectors make the containing set ambiguous only if a sector's polygon
        encloses another without sharing its boundary; the smallest box wins then.
        """
        found = self.sectors_at(x, y)
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        return min(found, key=lambda s: (self._sector_box[s][2] - self._sector_box[s][0]) *
                   (self._sector_box[s][3] - self._sector_box[s][1]))

    def near_sector_boundary(self, x, y, tol=1.5):
        for s in self._sector_grid.query_point(x, y):
            for a, b in self._sector_lines[s]:
                if dist_point_segment((x, y), a, b) <= tol:
                    return True
        return False


class LineOfSight:
    """2.5D sight between two points against map.json, after Doom's P_CheckSight.

    The sight line runs from an eye at height z0 to a target spanning heights [zb, zt]. Every
    linedef it crosses narrows the window of visible slopes: a one-sided line blocks; a two-sided
    line blocks unless its opening (the higher floor to the lower ceiling) leaves part of the target
    in view. Things never block sight. Heights are static map.json values unless `live` gives
    {sector: (floor, ceiling)}, so a door closed in the map data is closed here.
    """

    def __init__(self, map_data):
        self.floor = [s["floor"] for s in map_data["sectors"]]
        self.ceil = [s["ceiling"] for s in map_data["sectors"]]
        self.lines = [((l["x1"], l["y1"]), (l["x2"], l["y2"]), l["front_sector"], l["back_sector"], l["two_sided"])
                      for l in map_data["linedefs"]]
        self.grid = Grid(map_data["bounds"], 128)
        for ln in self.lines:
            self.grid.add_box(ln, ln[0][0], ln[0][1], ln[1][0], ln[1][1])

    def _heights(self, s, live):
        if live and s in live:
            return live[s]
        return self.floor[s], self.ceil[s]

    def clear(self, x0, y0, z0, x1, y1, zb, zt, live=None):
        dx, dy = x1 - x0, y1 - y0
        total = math.hypot(dx, dy)
        if total < 1e-6:
            return True
        top, bottom = (zt - z0) / total, (zb - z0) / total
        for (a, b, front, back, two) in self.grid.query_box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)):
            wx, wy = b[0] - a[0], b[1] - a[1]
            den = dx * wy - dy * wx
            if abs(den) < 1e-12:
                continue
            qx, qy = a[0] - x0, a[1] - y0
            s = (qx * wy - qy * wx) / den  # fraction along the sight line
            u = (qx * dy - qy * dx) / den  # fraction along the linedef
            if not (0.0 < s < 1.0 and 0.0 <= u <= 1.0):
                continue
            if not two or front is None or back is None:
                return False
            ff, fc = self._heights(front, live)
            bf, bc = self._heights(back, live)
            lo, hi = max(ff, bf), min(fc, bc)
            if hi <= lo:
                return False
            dist = s * total
            if ff != bf:
                bottom = max(bottom, (lo - z0) / dist)
            if fc != bc:
                top = min(top, (hi - z0) / dist)
            if top <= bottom:
                return False
        return True
