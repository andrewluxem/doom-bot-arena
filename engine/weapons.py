"""Weapon names, engine slots, and the per-bot armory every controller selects through.

Every player runs with neverswitchonpickup, so the held weapon only changes when the
controller selects one (`use <Class>`) or the engine switches away from an empty weapon.
`chosen` is the weapon the bot itself last selected: tics.jsonl `weapon_name`.
"""

import math

# characters/README.md: name -> (engine slot, ZDoom class, ammo slot or None for melee).
WEAPONS = {
    "fist": (1, "Fist", None),
    "chainsaw": (1, "Chainsaw", None),
    "pistol": (2, "Pistol", 2),
    "shotgun": (3, "Shotgun", 3),
    "super_shotgun": (3, "SuperShotgun", 3),
    "chaingun": (4, "Chaingun", 4),
    "rocket_launcher": (5, "RocketLauncher", 5),
    "plasma_rifle": (6, "PlasmaRifle", 6),
    "bfg": (7, "BFG9000", 7),
}
NAMES = tuple(WEAPONS)
SPAWN_WEAPONS = ("fist", "pistol")
# Ammo a shot needs (the super shotgun fires two shells, the BFG 40 cells).
AMMO_PER_SHOT = {"pistol": 1, "shotgun": 1, "super_shotgun": 2, "chaingun": 1, "rocket_launcher": 1,
                 "plasma_rifle": 1, "bfg": 40}
SWITCH_RETRY_TICS = 35  # re-send the selection if the engine slot has not followed by then


def slot_of(name):
    return WEAPONS[name][0]


def resolve(allowed, weights, present):
    """Allowed weapons the bot can hold on this map, best first.

    present is map.json weapons_present; fist and pistol are always held at spawn.
    """
    held = set(present) | set(SPAWN_WEAPONS)
    names = [w for w in allowed if w in held]
    return sorted(names, key=lambda w: (-float(weights.get(w, 0.0)), NAMES.index(w)))


class Armory:
    """Tracks which weapons this player owns and sends weapon selections.

    WEAPONn counts the weapons owned in slot n. Slot 3 at count 1 could be either shotgun:
    the pickup is attributed to the nearest shotgun or super shotgun thing in map.json,
    since weapons stay in place in deathmatch (sv_weaponstay) and pickups happen on them.
    """

    def __init__(self, map_weapons):
        self.things = [(w["name"], w["x"], w["y"]) for w in map_weapons if w.get("in_deathmatch", True)]
        self.owned = set(SPAWN_WEAPONS)
        self.counts = {}
        self.chosen = None
        self.sent_at = None
        self.alive = None

    def update(self, t, v):
        alive = v["DEAD"] == 0
        counts = {n: int(v[f"WEAPON{n}"]) for n in range(1, 8)}
        if alive and self.alive is False:  # respawned with fist and pistol
            self.owned, self.counts, self.chosen, self.sent_at = set(SPAWN_WEAPONS), {}, None, None
        self.alive = alive
        if not alive:
            return
        if not self.counts:  # first look after a spawn: slots 1 and 2 hold the fist and pistol
            self.counts = {n: (1 if n in (1, 2) else 0) for n in range(1, 8)}
        x, y = v["POSITION_X"], v["POSITION_Y"]
        for n, c in counts.items():
            if c <= self.counts.get(n, 0):
                continue
            if n == 1:
                self.owned.add("chainsaw")  # the second weapon in slot 1
            elif n == 3:
                if c >= 2:
                    self.owned |= {"shotgun", "super_shotgun"}
                else:
                    self.owned.add(self._nearest(("shotgun", "super_shotgun"), x, y) or "shotgun")
            else:
                self.owned |= {name for name, (s, _, _) in WEAPONS.items() if s == n}
        self.counts = counts

    def _nearest(self, names, x, y, radius=160):
        best = None
        for name, wx, wy in self.things:
            d = math.hypot(wx - x, wy - y)
            if name in names and d <= radius and (best is None or d < best[0]):
                best = (d, name)
        return best[1] if best else None

    def ammo(self, name, v):
        ammo_slot = WEAPONS[name][2]
        return math.inf if ammo_slot is None else int(v[f"AMMO{ammo_slot}"])

    def usable(self, name, v):
        return name in self.owned and self.ammo(name, v) >= AMMO_PER_SHOT.get(name, 0)

    def ready(self, v):
        """The engine holds the chosen weapon's slot (shared slots trust the selection)."""
        return self.chosen is not None and int(v["SELECTED_WEAPON"]) == slot_of(self.chosen)

    def select(self, t, name, v):
        """Commands to hold `name` (or nothing, when name is None). Returns console commands."""
        if name is None:
            self.chosen, self.sent_at = None, None
            return []
        if name != self.chosen:
            self.chosen, self.sent_at = name, t
            return [f"use {WEAPONS[name][1]}"]
        if not self.ready(v) and t - (self.sent_at or t) >= SWITCH_RETRY_TICS:
            self.sent_at = t
            return [f"use {WEAPONS[name][1]}"]
        return []
