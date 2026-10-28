"""Situation-dependent weapon fitness, after van Waveren's fuzzy weapon weights (2001, ch. 9 and 13.2).

A character's preference for a weapon is its sheet weight times a fitness that depends on
the distance to the enemy and the ammunition held, evaluated by linear interpolation between
breakpoints. The breakpoints describe the weapons, not any character, so they are shared.
"""

import math

# distance (map units) -> fitness. Shotguns fall off with spread; the rocket launcher is
# worthless inside its own splash radius (128); hitscan weapons hold up at range.
RANGE = {
    "fist": [(0, 1.0), (64, 1.0), (80, 0.0)],
    "chainsaw": [(0, 1.0), (72, 1.0), (88, 0.0)],
    "pistol": [(0, 0.35), (600, 0.3), (1500, 0.15)],
    "shotgun": [(0, 1.0), (300, 0.9), (600, 0.5), (1000, 0.2)],
    "super_shotgun": [(0, 1.0), (200, 1.0), (400, 0.5), (700, 0.1)],
    "chaingun": [(0, 0.7), (300, 0.9), (800, 0.9), (1500, 0.6)],
    "rocket_launcher": [(0, 0.0), (128, 0.05), (200, 0.9), (600, 1.0), (1200, 0.8)],
    "plasma_rifle": [(0, 0.9), (600, 1.0), (1200, 0.7)],
    "bfg": [(0, 0.2), (200, 1.0), (1200, 1.0)],
}
# ammo held -> fitness multiplier (van Waveren's lightning gun weight steps with ammo the same way)
AMMO = {
    "pistol": [(0, 0.0), (1, 0.4), (20, 1.0)],
    "shotgun": [(0, 0.0), (1, 0.4), (8, 1.0)],
    "super_shotgun": [(0, 0.0), (1, 0.0), (2, 0.4), (10, 1.0)],
    "chaingun": [(0, 0.0), (1, 0.4), (30, 1.0)],
    "rocket_launcher": [(0, 0.0), (1, 0.5), (5, 1.0)],
    "plasma_rifle": [(0, 0.0), (1, 0.4), (40, 1.0)],
    "bfg": [(0, 0.0), (39, 0.0), (40, 1.0)],
}
# Berserk multiplies fist damage by 10 (2-20 becomes 20-200 a punch), which makes the fist the
# strongest melee weapon while it lasts: its fitness is scaled by this.
BERSERK_FIST = 3.0
# Projectile speeds (map units per tic) for leading the aim; hitscan weapons are absent.
PROJECTILE_SPEED = {"rocket_launcher": 20.0, "plasma_rifle": 25.0, "bfg": 25.0}
# Extra fire tolerance (degrees) for spread weapons: a pellet cone forgives some aim error.
SPREAD_DEG = {"shotgun": 4.0, "super_shotgun": 7.0, "chaingun": 1.0, "pistol": 0.5, "rocket_launcher": 1.0,
              "plasma_rifle": 1.0, "fist": 8.0, "chainsaw": 8.0, "bfg": 2.0}


def interp(points, x):
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def fitness(name, distance, ammo, berserk=False):
    f = interp(RANGE[name], distance)
    if name in AMMO:
        f *= interp(AMMO[name], 0 if ammo is None else (1e9 if math.isinf(ammo) else ammo))
    if berserk and name == "fist":
        f *= BERSERK_FIST
    return f


def in_reach(name, distance):
    """A weapon is fired only where it can do something: melee weapons within their reach."""
    return interp(RANGE[name], distance) > 0


def choose(candidates, weights, distance, ammo_of, current=None, hysteresis=1.15, berserk=False):
    """Best weapon by sheet weight x fitness. The held weapon wins ties within `hysteresis`,
    because a switch costs about half a second. Returns (name or None, {name: score})."""
    scores = {w: float(weights.get(w, 0.0)) * fitness(w, distance, ammo_of(w), berserk) for w in candidates}
    best = max(scores, key=scores.get, default=None)
    if best is None or scores[best] <= 0:
        return None, scores
    if current in scores and scores[current] > 0 and scores[current] * hysteresis >= scores[best]:
        return current, scores
    return best, scores
