"""Rocket fire safety: would a rocket fired now explode inside its own splash?

A rocket (radius 11, height 8) leaves at the shooter's z + 32 along the view yaw and pitch.
Before firing, three rays are traced from the shooter against map.json: the centre line
and two at the rocket's radius either side. A ray stops at:
- a one-sided wall;
- a two-sided line whose far side does not leave the rocket's height open (a step up, a low
  ceiling, a closed door);
- the floor or ceiling of the sector it is crossing;
- a solid thing (map.json `obstacles`, as boxes).

The bot does not fire when any of these is within FLIGHT_CHECK (160) units of the muzzle.
That covers the splash radius (128) around the muzzle and the first 160 units of flight.

Two more refusals follow Doom's own splash rule (P_RadiusAttack): a thing takes splash when
max(|dx|, |dy|) from the blast, less its radius, is under 128. For the 16-unit shooter the
blast must therefore land at box distance SPLASH_REACH (144) or more, which on a diagonal is
up to 204 units, beyond 160.
- No impact (wall, floor, ceiling, thing) may land inside that reach.
- Visible players are obstacles too: each is moved along its seen velocity, and the rocket
  meets it when their boxes touch. A target charging the shooter, or a bystander between
  shooter and target, can bring the blast inside the reach. One match on seed 104 still had
  7 self-kills from exactly these after the wall check alone (11 before it).
"""

import math

from engine.geometry import Grid, MapGeometry, orient

SPLASH_RADIUS = 128.0
FLIGHT_CHECK = 160.0
ROCKET_RADIUS = 11.0
ROCKET_HEIGHT = 8.0
ROCKET_SPEED = 20.0  # units per tic
MUZZLE_HEIGHT = 32.0
PLAYER_RADIUS = 16.0
SPLASH_REACH = SPLASH_RADIUS + PLAYER_RADIUS
PLAYER_HORIZON = 24  # tics of rocket flight checked against visible players


class RocketCheck:
    def __init__(self, map_data):
        self.geo = MapGeometry(map_data)
        self.floor = [s["floor"] for s in map_data["sectors"]]
        self.ceil = [s["ceiling"] for s in map_data["sectors"]]
        self.lines = [((l["x1"], l["y1"]), (l["x2"], l["y2"]), l["front_sector"], l["back_sector"], l["two_sided"])
                      for l in map_data["linedefs"]]
        self.grid = Grid(map_data["bounds"], 128)
        for ln in self.lines:
            self.grid.add_box(ln, ln[0][0], ln[0][1], ln[1][0], ln[1][1])
        self.obstacles = [(o["x"], o["y"], o["radius"]) for o in map_data.get("obstacles", []) if o["in_deathmatch"]]

    def _heights(self, s, live):
        if live and s in live:
            return live[s]
        return self.floor[s], self.ceil[s]

    def first_impact(self, ox, oy, z0, dx, dy, slope, live=None, limit=FLIGHT_CHECK):
        """Distance along the ray to the first thing a rocket would hit, or None within `limit`."""
        sector = self.geo.sector_at(ox, oy)
        if sector is None:
            return 0.0
        ex, ey = ox + dx * limit, oy + dy * limit
        hits = []
        for (a, b, front, back, two) in self.grid.query_box(min(ox, ex), min(oy, ey), max(ox, ex), max(oy, ey)):
            wx, wy = b[0] - a[0], b[1] - a[1]
            den = dx * wy - dy * wx
            if abs(den) < 1e-12:
                continue
            qx, qy = a[0] - ox, a[1] - oy
            s = (qx * wy - qy * wx) / den
            u = (qx * dy - qy * dx) / den
            if 0 < s <= limit and 0 <= u <= 1:
                hits.append((s, a, b, front, back, two))
        hits.sort(key=lambda h: h[0])
        start = 0.0
        for s, a, b, front, back, two in hits:
            hit = self._floor_or_ceiling(sector, z0, slope, start, s, live)
            if hit is not None:
                return hit
            if not two:
                return s
            far = back if orient(a, b, (ox, oy)) < 0 else front  # front is right of v1 -> v2
            if far is None:
                return s
            f, c = self._heights(far, live)
            z = z0 + slope * s
            if z < f or z + ROCKET_HEIGHT > c:
                return s
            sector, start = far, s
        return self._floor_or_ceiling(sector, z0, slope, start, limit, live)

    def _floor_or_ceiling(self, sector, z0, slope, s0, s1, live):
        f, c = self._heights(sector, live)
        if slope < 0 and z0 + slope * s1 < f:
            return max(s0, (f - z0) / slope)
        if slope > 0 and z0 + slope * s1 + ROCKET_HEIGHT > c:
            return max(s0, (c - ROCKET_HEIGHT - z0) / slope)
        return None

    def clear(self, x, y, z, yaw_deg, pitch_up_deg=0.0, live=None, players=()):
        """(ok, distance to the nearest unsafe impact or None) for a rocket fired from (x, y, z) now.

        players: visible live players as (x, y, vx, vy), from the shooter's labels buffer.
        """
        dx, dy = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
        slope = math.tan(math.radians(max(-89.0, min(89.0, pitch_up_deg))))
        z0 = z + MUZZLE_HEIGHT
        nearest = None
        for off in (0.0, ROCKET_RADIUS, -ROCKET_RADIUS):
            ox, oy = x - dy * off, y + dx * off
            d = self.first_impact(ox, oy, z0, dx, dy, slope, live)
            if d is not None and (nearest is None or d < nearest):
                nearest = d
        for cx, cy, r in self.obstacles:
            d = _ray_box(x, y, dx, dy, cx, cy, r + ROCKET_RADIUS, FLIGHT_CHECK)
            if d is not None and (nearest is None or d < nearest):
                nearest = d
        if nearest is not None:
            return False, nearest
        # Doom's box-distance splash: impacts further than 160 can still reach on a diagonal
        reach = 2 * SPLASH_REACH
        d = self.first_impact(x, y, z0, dx, dy, slope, live, limit=reach)
        if d is not None and max(abs(dx * d), abs(dy * d)) < SPLASH_REACH:
            return False, d
        for cx, cy, r in self.obstacles:
            d = _ray_box(x, y, dx, dy, cx, cy, r + ROCKET_RADIUS, reach)
            if d is not None and max(abs(dx * d), abs(dy * d)) < SPLASH_REACH:
                return False, d
        for px, py, vx, vy in players:
            for k in range(1, PLAYER_HORIZON + 1):
                rx, ry = x + dx * ROCKET_SPEED * k, y + dy * ROCKET_SPEED * k
                qx, qy = px + vx * k, py + vy * k
                if max(abs(rx - qx), abs(ry - qy)) <= PLAYER_RADIUS + ROCKET_RADIUS:
                    if max(abs(rx - x), abs(ry - y)) < SPLASH_REACH:
                        return False, ROCKET_SPEED * k
                    break
        return True, None


def _ray_box(x, y, dx, dy, cx, cy, half, limit):
    """Entry distance of the ray into the axis-aligned square, or None within limit."""
    t0, t1 = 0.0, limit
    for o, d, lo, hi in ((x, dx, cx - half, cx + half), (y, dy, cy - half, cy + half)):
        if abs(d) < 1e-12:
            if not lo < o < hi:
                return None
            continue
        a, b = (lo - o) / d, (hi - o) / d
        if a > b:
            a, b = b, a
        t0, t1 = max(t0, a), min(t1, b)
        if t0 > t1:
            return None
    return t0
