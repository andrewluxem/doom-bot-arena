"""Placeholder bot (milestone 1A filler).

Not the personality engine: it only uses the sheet's max turn rate. It roams,
turns away when stuck, and shoots at the nearest visible player. Its RNG is
seeded from (match seed, slot). utility_scores is always null.

Since milestone 2A every player runs with neverswitchonpickup, so the placeholder
selects its own weapon: the best one it holds with ammo, in FILLER_ORDER. That
order is the filler's, not its sheet's.
"""

import math
import random

from engine.personality.perception import name_labels, player_labels
from engine.weapons import Armory

FILLER_ORDER = ("plasma_rifle", "super_shotgun", "chaingun", "shotgun", "rocket_launcher", "chainsaw", "pistol", "fist")


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


class PlaceholderBot:
    def __init__(self, slot, sheet, seed, map_data=None):
        self.rng = random.Random(f"{seed}:{slot}")
        self.armory = Armory((map_data or {}).get("weapons", []))
        self.identity = None  # set by engine/player.py; used only through perception
        self.max_turn = float(sheet["perception"]["max_turn_deg_per_tic"])
        self.trail = []
        self.turn_left = 0
        self.turn_rate = 0.0
        self.strafe = None
        self.strafe_left = 0

    def act(self, tic, state, v):
        x, y, ang = v["POSITION_X"], v["POSITION_Y"], v["ANGLE"]
        self.armory.update(tic, v)
        best = next((w for w in FILLER_ORDER if self.armory.usable(w, v)), "fist")
        cmds = self.armory.select(tic, best, v) if v["DEAD"] == 0 else []
        info = {"weapon_name": self.armory.chosen, "target_slot": None}
        enemy = self._nearest_enemy(tic, state, x, y)
        if enemy is not None:
            ex, ey, dist, slot = enemy
            err = wrap180(math.degrees(math.atan2(ey - y, ex - x)) - ang)
            action = {"TURN_LEFT_RIGHT_DELTA": -max(-self.max_turn, min(self.max_turn, err)),
                      "ATTACK": 1 if abs(err) < 6 else 0}
            if dist > 256:
                action["MOVE_FORWARD"] = 1
            self._strafe(action)
            info["target_slot"] = slot
            return action, cmds, "IN_COMBAT", None, info
        return self._roam(tic, x, y), cmds, "ROAMING", None, info

    def _nearest_enemy(self, tic, state, x, y):
        players, _ = player_labels(state, x, y)
        names = name_labels(self.identity, tic, players)
        best = None
        for lab in players:
            d = math.hypot(lab.object_position_x - x, lab.object_position_y - y)
            if best is None or d < best[2]:
                best = (lab.object_position_x, lab.object_position_y, d, names[lab.object_id])
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
