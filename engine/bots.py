"""Placeholder bot for milestone 1A.

Not the personality engine: it only uses the sheet's max turn rate. It roams,
turns away when stuck, and shoots at the nearest visible player. Its RNG is
seeded from (match seed, slot). utility_scores is always null.
"""

import math
import random

ENEMY = "DoomPlayer"


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


class PlaceholderBot:
    def __init__(self, slot, sheet, seed):
        self.rng = random.Random(f"{seed}:{slot}")
        self.max_turn = float(sheet["perception"]["max_turn_deg_per_tic"])
        self.trail = []
        self.turn_left = 0
        self.turn_rate = 0.0
        self.strafe = None
        self.strafe_left = 0

    def act(self, tic, state, v):
        x, y, ang = v["POSITION_X"], v["POSITION_Y"], v["ANGLE"]
        enemy = self._nearest_enemy(state, x, y)
        if enemy is not None:
            ex, ey, dist = enemy
            err = wrap180(math.degrees(math.atan2(ey - y, ex - x)) - ang)
            action = {"TURN_LEFT_RIGHT_DELTA": -max(-self.max_turn, min(self.max_turn, err)),
                      "ATTACK": 1 if abs(err) < 6 else 0}
            if dist > 256:
                action["MOVE_FORWARD"] = 1
            self._strafe(action)
            return action, [], "IN_COMBAT", None
        return self._roam(tic, x, y), [], "ROAMING", None

    def _nearest_enemy(self, state, x, y):
        best = None
        for lab in state.labels or []:
            if lab.object_name != ENEMY:
                continue
            d = math.hypot(lab.object_position_x - x, lab.object_position_y - y)
            if d > 1 and (best is None or d < best[2]):
                best = (lab.object_position_x, lab.object_position_y, d)
        return best

    def _strafe(self, action):
        if self.strafe_left <= 0:
            self.strafe = self.rng.choice(["MOVE_LEFT", "MOVE_RIGHT", None])
            self.strafe_left = self.rng.randint(10, 30)
        self.strafe_left -= 1
        if self.strafe:
            action[self.strafe] = 1

    def _roam(self, tic, x, y):
        self.trail.append((x, y))
        if len(self.trail) > 10:
            self.trail.pop(0)
        stuck = len(self.trail) == 10 and math.dist(self.trail[0], self.trail[-1]) < 8
        if self.turn_left <= 0 and (stuck or self.rng.random() < 0.01):
            self.turn_left = self.rng.randint(6, 14)
            self.turn_rate = self.rng.choice([-1, 1]) * self.max_turn
            self.trail.clear()
        action = {"MOVE_FORWARD": 1}
        if self.turn_left > 0:
            action["TURN_LEFT_RIGHT_DELTA"] = self.turn_rate
            self.turn_left -= 1
        if tic % 20 == 0:
            action["USE"] = 1  # open doors
        return action
