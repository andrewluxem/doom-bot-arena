"""Personality bot: one engine for every character, driven only by its sheet's dials.

Per tic it perceives (labels buffer only), aims and moves. At 10 Hz (every 3 or 4 tics)
it re-scores the behaviors (utility.py), picks a target and a weapon (fuzzy.py), and
re-plans its goal on the nav graph (nav.py). Aim is limited by the three perception
dials: reaction_time_ms before acting on a new sighting, aim_accuracy as angular jitter
resampled each decision, and max_turn_deg_per_tic on every turn and pitch change.

Milestone 2B mechanics, each driven by a sheet field (characters/README.md):
  item timing      items.item_timing: respawn timers from own pickups and from item labels seen
                   to vanish or reappear; item goals and roaming aim to arrive as an item respawns
  retreat band     temperament.retreat_distance: inside it, back away from the target on a clear
                   heading while still aiming
  flank            temperament.flank: paths toward a known enemy cost more inside its facing cone,
                   the goal moves behind it, and in a fight the bot strafes out of the cone
  re-roll          ranges, reroll_interval_s, reroll_seed_from_match: every interval the dials under
                   `ranges` are drawn again, from the match seed
  melee, berserk   melee weapons fire only within reach; items.weights.berserk lures the bot to a
                   berserk pack, which makes the fist its best weapon until it dies

Any bot spawned into a pocket it cannot leave (its reachable nav area under TRAPPED_NODES; on E1M5
the exit room) uses the pocket's exit switch when no enemy is in view. Under sv_noexit that kills it
(OB_EXIT, a self-kill) and it respawns elsewhere, as a player would. Otherwise it never uses an exit.
A pocket with no exit switch (MAP10's, left in play only by an S1 floor switch the nav graph does
not model) gets the console suicide instead (`kill`, OB_SUICIDE), once per life.

The RNG is seeded from (match seed, slot).
"""

import copy
import json
import math
import os
import random

from engine import items as itemfacts
from engine import weapons
from engine.geometry import EYE_HEIGHT, LineOfSight
from engine.personality import fuzzy, nav, utility
from engine.personality.ballistics import RocketCheck
from engine.personality import spots as spotlib
from engine.personality.perception import Perception, distance

TICRATE = 35
COMBAT_MEMORY = 21  # tics an enemy stays the target after leaving view
THREAT_MEMORY = 35
RECENT_CONTACT = 6 * TICRATE
JITTER_DEG = 10.0  # aim error standard deviation at aim_accuracy 0
EYE = 41.0
CHEST = 32.0
ITEM_RESPAWN = itemfacts.RESPAWN_TICS
ITEM_VALUE = {"Stimpack": 10, "Medikit": 25, "HealthBonus": 1, "Soulsphere": 100, "Megasphere": 200,
              "GreenArmor": 100, "BlueArmor": 200, "ArmorBonus": 1}
AMMO_ITEMS = {"pistol": ("Clip", "ClipBox"), "chaingun": ("Clip", "ClipBox"), "shotgun": ("Shell", "ShellBox"),
              "super_shotgun": ("Shell", "ShellBox"), "rocket_launcher": ("RocketAmmo", "RocketBox"),
              "plasma_rifle": ("Cell", "CellPack"), "bfg": ("Cell", "CellPack")}
ARRIVE = 28
SPOT_ARRIVE = 32
SPOT_LEAVE = 96  # once arrived, the bot holds until pushed this far off its spot
FRICTION_SLIDE = 9.7  # a running player coasts about 9.7 tics of its speed (ZDoom friction 0.90625)
REPLAN_TICS = 3 * TICRATE
GOAL_TIMEOUT = 20 * TICRATE
LIFT_GIVE_UP = 8 * TICRATE
BLOCK_TICS = 15 * TICRATE
RUN_SPEED = 15.0  # units per tic for planning arrivals; a running player tops out near 16.7
ITEM_VIEW = 1536  # how far an item-timing bot checks item places it can see
ITEM_FOV = 42.0  # half-angle, inside the 45-degree half view so an item at the edge is not misread
ITEM_LABEL_MATCH = 24
TIMED_WAIT_MAX = 10 * TICRATE  # an item-timing bot will wait up to this long at an item
WAIT_RADIUS = 40
WAIT_GRACE = 2 * TICRATE  # ZDoom brings an item back a tic or two after RESPAWN_TICS; wait past the window
LURE_CLASSES = ("berserk",)  # one-off pickups a bot goes out of its way for when its sheet weights them
LURE_WEIGHT = 0.5
TOUCH = 36  # player half-size 16 + pickup radius 20: Doom touches boxes
FLANK_CONE = 45.0  # half-angle of the target's view cone
FLANK_RANGE = 1024
FLANK_PENALTY = 3.0  # extra cost factor of path edges into the target's cone
FLANK_REAR = 160  # the flank goal sits this far behind a known enemy
RETREAT_PROBE = 96
TRAPPED_NODES = 600  # E1M5's exit room is 291 nav nodes and MAP10's teleporter pocket 164; MAP10's
                     # smaller arena, 1634, is a place to fight in, not a trap
TRAPPED_WAIT = 3 * TICRATE


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def bearing(x0, y0, x1, y1):
    return math.degrees(math.atan2(y1 - y0, x1 - x0))


class Dials:
    """The sheet, read once. Nothing else differs between characters."""

    def __init__(self, sheet):
        p, tm = sheet["perception"], sheet["temperament"]
        self.reaction_tics = max(0, round(p["reaction_time_ms"] / 1000 * TICRATE))
        self.aim_accuracy = float(p["aim_accuracy"])
        self.jitter_deg = (1.0 - self.aim_accuracy) * JITTER_DEG
        self.lead = max(0.0, min(1.0, (self.aim_accuracy - 0.4) / 0.4))  # van Waveren: linear leading from 0.4
        self.max_turn = float(p["max_turn_deg_per_tic"])
        self.aggression = float(tm["aggression"])
        self.self_preservation = float(tm["self_preservation"])
        self.engage_health_min = float(tm["engage_health_min"])
        self.rmin, self.rmax = (float(r) for r in tm["preferred_range"])
        self.behavior_weights = dict(sheet["behavior_weights"])
        self.weapon_weights = dict(sheet["weapons"]["weights"])
        self.allowed = list(sheet["weapons"]["allowed"])
        self.item_weights = dict(sheet["items"]["weights"])
        sp = sheet.get("spots") or {}
        self.spot_mode = sp.get("mode")
        self.hold_label = sp.get("hold_label")
        self.fire_window_tics = (round(sp["fire_only_when_visible_ms"] / 1000 * TICRATE)
                                 if sp.get("fire_only_when_visible_ms") is not None else None)
        leave = {k: v for cond in sp.get("leave_when", []) for k, v in cond.items()}
        self.leave_health_below = leave.get("health_below")
        self.leave_no_contact_tics = (leave["no_contact_seconds"] * TICRATE) if "no_contact_seconds" in leave else None
        # How deep into its preferred range the bot pushes: an aggressive bot closes to rmin.
        self.desired_range = self.rmin + (self.rmax - self.rmin) * (1.0 - self.aggression)
        self.retreat_distance = tm.get("retreat_distance")
        self.flank = bool(tm.get("flank"))
        self.item_timing = bool(sheet["items"].get("item_timing"))


def with_values(sheet, values):
    """A copy of the sheet with each dotted path in `values` set (re-rolled dials)."""
    out = copy.deepcopy(sheet)
    for path, v in values.items():
        node, *rest = path.split(".")
        cur = out
        for key in [node] + rest[:-1]:
            cur = cur[key]
        cur[rest[-1] if rest else node] = v
    return out


def roll(sheet, rng):
    """One draw of every dial under the sheet's `ranges`, uniform in its [low, high]."""
    return {path: round(rng.uniform(lo, hi), 3) for path, (lo, hi) in sorted((sheet.get("ranges") or {}).items())}


def observe_item(it, t, there):
    """Update an item's respawn window from one look at its place. `seen` is the last tic it was seen in
    place, or None while it is gone (or never looked at); `back`..`back_hi` is when a gone item reappears."""
    if there:
        if it["seen"] is None:
            it["back"] = it["back_hi"] = t  # back
        it["seen"] = t
    elif it["seen"] is not None:  # it was there and is not: taken since it was last seen
        it["back"] = max(t, it["seen"] + ITEM_RESPAWN)
        it["back_hi"] = t + ITEM_RESPAWN
        it["seen"] = None
    elif t > it["back_hi"] + WAIT_GRACE:  # never seen, or overdue: it went at some time we did not see
        it["back"], it["back_hi"] = t, t + ITEM_RESPAWN
    else:  # gone and not due yet
        it["back"] = max(it["back"], t)


def pending(it, t):
    """A timed item not yet seen back, up to WAIT_GRACE past the end of its window."""
    return it["seen"] is None and t < it["back_hi"] + WAIT_GRACE


def expected_back(it):
    """When a timing bot expects the item: the respawn tic if known, else the middle of its window."""
    return (it["back"] + it["back_hi"]) / 2.0


def in_cone(px, py, cx, cy, facing, half=FLANK_CONE):
    """Is (px, py) inside the view cone of a player at (cx, cy) facing `facing` degrees?"""
    return abs(wrap180(bearing(cx, cy, px, py) - facing)) < half


class PersonalityBot:
    def __init__(self, slot, sheet, seed, map_data, cache_dir=None):
        self.slot, self.code = slot, sheet["code"]
        self.sheet = sheet
        self.d = Dials(sheet)
        self.rng = random.Random(f"{seed}:{slot}")
        interval = sheet.get("reroll_interval_s")
        self.reroll_tics = round(interval * TICRATE) if interval and sheet.get("ranges") else None
        self.reroll_seed = f"{seed}:{slot}:reroll" if sheet.get("reroll_seed_from_match") else None
        self.roll_epoch, self.rolls = 0, []
        self.berserk = False
        self.identity = None  # set by engine/player.py; used only by perception
        self.graph = nav.load(map_data, cache_dir)
        self.resolved = weapons.resolve(self.d.allowed, self.d.weapon_weights, map_data["weapons_present"])
        self.best_weight = max((self.d.weapon_weights.get(w, 0.0) for w in self.resolved), default=0.0)
        self.armory = weapons.Armory(map_data["weapons"])
        self.rocket = RocketCheck(map_data) if "rocket_launcher" in self.resolved else None
        self.rockets_held = 0  # shots withheld because the rocket would burst within its own splash
        self.perc = Perception(slot)
        self.spot_cands = []
        if self.d.spot_mode:
            req = [(self.d.spot_mode, self.d.rmin, self.d.rmax)]
            self.spot_cands = spotlib.table(map_data, cache_dir, req, self.graph)["candidates"][self.d.spot_mode]
        node = lambda x, y: self.graph.nearest(x, y, max_dist=128, need_clear=False)
        self.weapon_things = [(w["name"], w["x"], w["y"], node(w["x"], w["y"]))
                              for w in map_data["weapons"] if w["in_deathmatch"]]
        # back: earliest tic the item can be there; back_hi: latest; seen: last tic it was seen in place
        self.items = [{"name": i["name"], "class": i["class"], "x": i["x"], "y": i["y"], "back": 0, "back_hi": 0,
                       "seen": None, "node": node(i["x"], i["y"])} for i in map_data.get("items", []) if i["in_deathmatch"]]
        self.sight = None
        if self.d.item_timing:
            self.sight = LineOfSight(map_data)
            for it in self.items:
                n = it["node"]
                it["z"] = self.graph.floor[self.graph.nodes[n][2]] if n is not None else 0.0
        self.wait_item = None  # the item a timing bot is waiting at for its respawn
        self.exits = []  # (line id, approach point, midpoint) of every exit switch line
        for l in map_data["linedefs"]:
            if l.get("exit") and l["special"] in (11, 51):  # S1 exit, S1 secret exit: used, not walked over
                mx, my = (l["x1"] + l["x2"]) / 2, (l["y1"] + l["y2"]) / 2
                ln = math.hypot(l["x2"] - l["x1"], l["y2"] - l["y1"]) or 1.0
                nx, ny = (l["y2"] - l["y1"]) / ln, -(l["x2"] - l["x1"]) / ln  # toward the front (right) side
                self.exits.append((l["id"], (mx + 40 * nx, my + 40 * ny), (mx, my)))
        self.spawned_at, self.suicide_sent = 0, False
        self.attractors = ([(s["x"], s["y"], node(s["x"], s["y"])) for s in map_data["deathmatch_starts"]]
                           + [(w[1], w[2], w[3]) for w in self.weapon_things])
        for c in self.spot_cands:
            c["node"] = c["id"]
        self._dmap, self._dmap_t = {}, None
        self.searched = {}  # contact key -> last_seen tic whose position the bot has already checked
        self.goal_unreachable = False
        self.blocked = {}  # (u, v) nav edge -> tic until which it is skipped (the bot got stuck on it)
        self.alive = None
        self.behavior, self.scores = "ROAMING", None
        self.target, self.engaging, self.weapon_choice = None, False, None
        self.jit_yaw = self.jit_pitch = 0.0
        self.strafe, self.goal, self.goal_tag, self.goal_set_at = 0, None, None, 0
        self.path, self.path_i, self.planned_at = None, 0, -10 ** 9
        self.spot, self.at_spot, self.spot_since, self.spot_left = None, False, 0, {}
        self.spot_log = []
        self.prev = None  # (health, armor, ammo tuple) on the previous alive tic
        self.trail = []
        self.unstick_until, self.unstick_dir, self.stuck_count = -1, 0, 0
        self.look_back_until, self.look_back_dir = -1, 0.0
        self.lift_wait_since, self.lift_blocked_until = None, -1
        self.use_tic = -10 ** 9
        self.live, self.lift_floor, self.door_ceiling, self.heights = {}, {}, {}, {}
        self.ang = 0.0
        self.trace = [] if os.environ.get("DBA_TRACE") else None

    # ------------------------------------------------------------------ tic
    def act(self, t, st, v):
        self.armory.update(t, v)
        x, y, z, ang = v["POSITION_X"], v["POSITION_Y"], v["POSITION_Z"], v["ANGLE"]
        if v["DEAD"] != 0:
            if self.alive:
                self._on_death(t)
            self.alive = False
            return {}, [], "DEAD", None, {}
        if not self.alive:
            self._on_spawn(t)
        self.alive = True
        if self.reroll_tics and t // self.reroll_tics != self.roll_epoch:
            self._reroll(t, t // self.reroll_tics)
        self.perc.update(t, st, self.identity, x, y)
        self._watch_self(t, v, x, y)
        decided = (t * 10) // TICRATE != ((t - 1) * 10) // TICRATE or self.scores is None
        if decided:
            self._read_live(st)
            if self.d.item_timing:
                self._observe_items(t, st, x, y, z, ang)
            self._decide(t, v, x, y)
        cmds = self.armory.select(t, self.weapon_choice, v)
        action = self._execute(t, v, x, y, z, ang)
        if decided and self._suicide_now(t, x, y):
            cmds = cmds + ["kill"]
        tgt = self.target.slot if (self.engaging and self.target is not None) else None
        return action, cmds, self.behavior, self.scores, {"weapon_name": self.armory.chosen, "target_slot": tgt}

    def finish(self, outdir, n_tics):
        if self.trace is not None:
            with open(os.path.join(outdir, "trace.jsonl"), "w") as f:
                for rec in self.trace:
                    f.write(json.dumps(rec) + "\n")
        if self.spot_log and self.spot_log[-1]["to_tic"] is None:
            self.spot_log[-1]["to_tic"] = n_tics - 1
        with open(os.path.join(outdir, "spots_chosen.json"), "w") as f:
            json.dump(self.spot_log, f)
        if self.reroll_tics:
            with open(os.path.join(outdir, "rerolls.json"), "w") as f:
                json.dump(self.rolls, f)

    def _reroll(self, t, epoch):
        """Draw the dials under `ranges` again: from (match seed, slot, epoch) when the sheet says so, so
        a replay draws the same values at the same tics. The sheet's own values hold until the first roll."""
        rng = random.Random(f"{self.reroll_seed}:{epoch}") if self.reroll_seed else random.Random()
        values = roll(self.sheet, rng)
        self.d = Dials(with_values(self.sheet, values))
        self.roll_epoch = epoch
        self.rolls.append({"tic": t, "epoch": epoch, "values": values})

    # ------------------------------------------------------------------ lifecycle
    def _on_spawn(self, t):
        self.perc.reset()
        self.target, self.engaging, self.weapon_choice = None, False, None
        self.goal, self.path, self.spot, self.at_spot = None, None, None, False
        self.prev, self.trail, self.stuck_count = None, [], 0
        self.scores, self.berserk, self.wait_item = None, False, None
        self.spawned_at, self.suicide_sent = t, False

    def _on_death(self, t):
        self._close_spot(t - 1)

    def _read_live(self, st):
        """Current ceiling of remote doors and floor of lifts (sectors info is static geometry otherwise)."""
        secs = st.sectors or []
        live = {}
        for s in self.graph.remote_doors:
            if s < len(secs):
                live[s] = secs[s].ceiling_height
        self.live = live
        self.lift_floor = {s: secs[s].floor_height for s in self.graph.lift_sectors if s < len(secs)}
        self.door_ceiling = {s: secs[s].ceiling_height for s in self.graph.manual_doors if s < len(secs)}
        moving = (self.graph.manual_doors | self.graph.remote_doors | self.graph.lift_sectors)
        self.heights = {s: (secs[s].floor_height, secs[s].ceiling_height) for s in moving if s < len(secs)}

    def _watch_self(self, t, v, x, y):
        """Pickups (to time item respawns), damage taken while no enemy is in view, and getting stuck."""
        ammo = tuple(int(v[f"AMMO{n}"]) for n in range(2, 7))
        cur = (v["HEALTH"], v["ARMOR"], ammo)
        if self.prev is not None:
            hp0, ar0, am0 = self.prev
            if cur[0] > hp0:
                self._mark_taken(t, x, y, "health")
            if cur[1] > ar0:
                self._mark_taken(t, x, y, "armor")
            if any(a > b for a, b in zip(ammo, am0)):
                self._mark_taken(t, x, y, "ammo")
            if cur[0] < hp0 and not self.perc.visible():
                self.look_back_until, self.look_back_dir = t + 25, v["ANGLE"] + 180.0
        for it in self.items:
            # a berserk pack is picked up on touch whatever the health, so it shows in no counter
            if it["class"] == "berserk" and it["back"] <= t and abs(it["x"] - x) < TOUCH and abs(it["y"] - y) < TOUCH:
                self.berserk = True
                it["back"] = it["back_hi"] = t + ITEM_RESPAWN
                it["seen"] = None
        self.prev = cur
        self.trail.append((x, y))
        if len(self.trail) > 24:
            self.trail.pop(0)

    def _mark_taken(self, t, x, y, cls):
        best = None
        for it in self.items:
            d = math.hypot(it["x"] - x, it["y"] - y)
            if it["class"] == cls and d < 64 and (best is None or d < best[0]):
                best = (d, it)
        if best:
            best[1]["back"] = best[1]["back_hi"] = t + ITEM_RESPAWN
            best[1]["seen"] = None

    def _observe_items(self, t, st, x, y, z, ang):
        """Item timing: item places in view (inside the view cone, in line of sight) either show the item's
        label or not. One seen to vanish was taken since it was last seen, so it is back within that window
        plus 30 s; one seen in place is back now."""
        labels = {}
        for lab in st.labels or []:
            labels.setdefault(lab.object_name, []).append((lab.object_position_x, lab.object_position_y))
        eye = z + EYE_HEIGHT
        for it in self.items:
            if it["name"] not in itemfacts.TIMED and it["class"] not in LURE_CLASSES:
                continue
            d = math.hypot(it["x"] - x, it["y"] - y)
            if d > ITEM_VIEW or (d > 24 and abs(wrap180(bearing(x, y, it["x"], it["y"]) - ang)) > ITEM_FOV):
                continue
            if d > 24 and not self.sight.clear(x, y, eye, it["x"], it["y"], it["z"], it["z"] + 16, live=self.heights):
                continue
            there = any(math.hypot(lx - it["x"], ly - it["y"]) <= ITEM_LABEL_MATCH for lx, ly in labels.get(it["name"], ()))
            observe_item(it, t, there)

    # ------------------------------------------------------------------ decide (10 Hz)
    def _decide(self, t, v, x, y):
        d = self.d
        health, armor = v["HEALTH"], v["ARMOR"]
        cands = [c for c in self.perc.recent(t, COMBAT_MEMORY) if not c.burned]
        target = None
        if cands:
            def rank(c):
                r = distance(c, x, y, t)
                return r * (0.75 if c is self.target else 1.0) + (0 if c.visible else 200)
            target = min(cands, key=rank)
        dist = distance(target, x, y, t) if target else None

        usable = [w for w in self.resolved if self.armory.usable(w, v)]
        has_usable = bool(usable)
        armed = any(d.weapon_weights.get(w, 0) >= 0.5 * self.best_weight for w in usable)
        choice, _ = fuzzy.choose(usable, d.weapon_weights, dist if dist is not None else d.desired_range,
                                 lambda w: self.armory.ammo(w, v), current=self.weapon_choice, berserk=self.berserk)
        self.weapon_choice = choice

        cornered = target is not None and dist < max(d.rmin, 128.0)
        if target is not None and d.fire_window_tics is not None and not cornered:
            if target.seen_for(t) > d.reaction_tics + d.fire_window_tics:
                target.burned = True  # the ambusher lets this sighting go
                target, dist = None, None
        health_ok = health >= d.engage_health_min or cornered
        self.target = target
        self.engaging = target is not None and has_usable and health_ok

        threat = bool(self.perc.recent(t, THREAT_MEMORY))
        latest = self.perc.latest()
        recent = latest is not None and not latest.visible and t - latest.last_seen <= RECENT_CONTACT
        sit = utility.Situation(
            health=health, armor=armor, has_usable=has_usable, armed=armed, engageable=self.engaging,
            threat=threat, cornered=cornered, recent_contact=recent,
            health_offer=self._offer(t, x, y, "health", 100 - health),
            armor_offer=self._offer(t, x, y, "armor", 100 - armor))
        self.scores = utility.scores(sit, d)
        prev_behavior = self.behavior
        self.behavior = utility.choose(self.scores, self.behavior)

        if d.spot_mode:
            self._manage_spot(t, x, y, health, has_usable)
        self._choose_goal(t, v, x, y, armed, usable, latest, prev_behavior)
        if self.trace is not None:
            self.trace.append({
                "t": t, "xy": [round(x), round(y)], "b": self.behavior, "hp": int(health), "armed": armed,
                "usable": usable, "w": self.weapon_choice, "owned": sorted(self.armory.owned),
                "tgt": None if target is None else [str(target.key), target.visible, round(dist or 0)],
                "contacts": [[str(c.key), c.visible, c.burned] for c in self.perc.contacts.values()],
                "goal": None if self.goal is None else [round(self.goal[0]), round(self.goal[1]), self.goal_tag],
                "path": None if not self.path else [len(self.path), self.path_i],
                "spot": None if self.spot is None else [self.spot["id"], self.at_spot], "stuck": self.stuck_count,
                "rockets_held": self.rockets_held})

        self.jit_yaw = self.rng.gauss(0.0, d.jitter_deg)
        self.jit_pitch = self.rng.gauss(0.0, d.jitter_deg * 0.5)
        if self.rng.random() < 0.08 + 0.17 * d.aggression:
            self.strafe = self.rng.choice((-1, 1)) if self.rng.random() < d.aggression else 0

    def _offer(self, t, x, y, cls, need):
        """0..1: value of the best available item of a class, discounted by straight-line distance.
        An item-timing bot also counts items due back by the time it could get there, the wait added."""
        best = 0.0
        for it in self.items:
            if it["class"] != cls:
                continue
            dist = math.hypot(it["x"] - x, it["y"] - y)
            wait = self._wait(t, it, dist / RUN_SPEED)
            if wait is None:
                continue
            gain = min(ITEM_VALUE.get(it["name"], 10), max(0, need))
            best = max(best, min(1.0, gain / 25.0) * math.exp(-(dist + RUN_SPEED * wait) / 1500.0))
        return best

    def _wait(self, t, it, eta):
        """Tics the bot would wait at the item after arriving in `eta` tics, or None if it will not be there.
        Without item timing an item counts only when the bot believes it is there now."""
        if not self.d.item_timing:
            return 0.0 if it["back"] <= t else None
        due = expected_back(it)
        if due > t + eta + TIMED_WAIT_MAX:
            return None
        return max(0.0, due - (t + eta))

    # ------------------------------------------------------------------ spots
    def _manage_spot(self, t, x, y, health, has_usable):
        d = self.d
        holding = self.behavior == d.hold_label
        if self.spot is not None:
            sx, sy = self.spot["x"], self.spot["y"]
            self.at_spot = math.hypot(sx - x, sy - y) <= (SPOT_LEAVE if self.at_spot else SPOT_ARRIVE)
            if self.at_spot and self.spot_since is None:
                self.spot_since = t
            quiet = t - max(self.perc.last_any_contact, self.spot_since or t)
            if (d.leave_no_contact_tics is not None and self.at_spot and quiet > d.leave_no_contact_tics):
                self._close_spot(t - 1)
        if self.spot is None and holding and has_usable:
            self._pick_spot(t, x, y)

    def _pick_spot(self, t, x, y):
        reach = self._reach(t, x, y)
        best = None
        for c in self.spot_cands:
            if self.spot_left.get(c["id"], -10 ** 9) > t - 30 * TICRATE or c["node"] not in reach:
                continue
            u = c["score"] - reach[c["node"]] / 2500.0 + self.rng.uniform(0.0, 0.08)
            if best is None or u > best[0]:
                best = (u, c)
        if best is None:
            return
        c = best[1]
        self.spot, self.at_spot, self.spot_since = c, False, None
        self.spot_log.append({"slot": self.slot, "code": self.code, "mode": self.d.spot_mode, "spot": c["id"],
                              "x": c["x"], "y": c["y"], "angle": c["watch_angle"], "from_tic": t, "to_tic": None})

    def _close_spot(self, t):
        if self.spot is not None:
            self.spot_left[self.spot["id"]] = t
            if self.spot_log and self.spot_log[-1]["to_tic"] is None:
                self.spot_log[-1]["to_tic"] = max(t, self.spot_log[-1]["from_tic"])
        self.spot, self.at_spot, self.spot_since = None, False, None

    # ------------------------------------------------------------------ goals
    def _reach(self, t, x, y):
        """Path cost from here to every node reachable now (closed remote doors excluded), once per decision."""
        if self._dmap_t != t:
            start = self.graph.nearest(x, y)
            allow = nav.USABLE_KINDS if t >= self.lift_blocked_until else nav.USABLE_KINDS - {"lift"}
            self._dmap = self.graph.dijkstra(start, allow=allow, live=self.live)[0] if start is not None else {}
            self._dmap_t = t
        return self._dmap

    def _choose_goal(self, t, v, x, y, armed, usable, latest, prev_behavior):
        b = self.behavior
        goal, tag = None, None
        if not self.perc.visible() and t - self.spawned_at > TRAPPED_WAIT:
            goal, tag = self._exit_goal(t, x, y)  # a pocket it cannot leave: nothing else there matters
        if goal is None and b not in ("IN_COMBAT", "FLEEING") and not armed:
            goal, tag = self._weapon_goal(t, x, y)
        if goal is None and b not in ("IN_COMBAT", "FLEEING") and usable and self._ammo_low(v, usable):
            goal, tag = self._item_goal(t, x, y, names=set(sum((AMMO_ITEMS.get(w, ()) for w in usable), ())) | {"Backpack"})
        if goal is None and b in ("ROAMING", "HUNTING") and (b == "ROAMING" or latest is None
                                                             or t - latest.last_seen > RECENT_CONTACT):
            goal, tag = self._lure_goal(t, x, y)
        if goal is None:
            if b == "IN_COMBAT":
                if self.target is not None and not self.target.visible:
                    goal, tag = self._enemy_goal(t, self.target), "last_seen"
            elif b == "HUNTING":
                if (latest is not None and t - latest.last_seen <= RECENT_CONTACT
                        and self.searched.get(latest.key) != latest.last_seen):
                    goal, tag = self._enemy_goal(t, latest), f"hunt:{latest.key}"
                    if math.hypot(goal[0] - x, goal[1] - y) < 64 or (self.goal_tag == tag and self.goal_unreachable):
                        # got there and nobody is in view, or there is no way there (seen through a window)
                        self.searched[latest.key] = latest.last_seen
                        goal, tag = self._random_attractor(t, x, y), "hunt"
                else:
                    goal, tag = self._idle_goal(t, x, y, "hunt")
            elif b == "ROAMING":
                goal, tag = self._idle_goal(t, x, y, "roam")
            elif b == self.d.hold_label and self.spot is not None:
                goal, tag = (self.spot["x"], self.spot["y"]), f"spot:{self.spot['id']}"
            elif b == "COLLECTING_HEALTH":
                goal, tag = self._item_goal(t, x, y, cls="health", need=100 - v["HEALTH"])
            elif b == "COLLECTING_ARMOR":
                goal, tag = self._item_goal(t, x, y, cls="armor", need=100 - v["ARMOR"])
            elif b == "FLEEING":
                goal, tag = self._item_goal(t, x, y, cls="health", need=100 - v["HEALTH"])
                if goal is None:
                    goal, tag = self._away_from_threat(t, x, y), "flee"
        self.wait_item = self._item_at(goal) if self.d.item_timing and tag and tag.startswith("item:") else None
        if goal is None:
            self.goal, self.goal_tag, self.path = None, None, None
            return
        if tag != self.goal_tag or self.goal is None or math.hypot(goal[0] - self.goal[0], goal[1] - self.goal[1]) > 96:
            self.goal, self.goal_tag, self.goal_set_at = goal, tag, t
            self._plan(t, x, y)
        elif t - self.planned_at > REPLAN_TICS:
            self._plan(t, x, y)

    def _flank_penalty(self, t):
        """Flank: path edges into the view cone of the enemy being approached cost more."""
        c = self.target if self.goal_tag == "last_seen" else None
        if self.goal_tag and self.goal_tag.startswith("hunt:"):
            c = next((k for k in self.perc.contacts.values() if f"hunt:{k.key}" == self.goal_tag), None)
        if not self.d.flank or c is None or c.angle is None:
            return None
        cx, cy = c.position_at(t)
        nodes, facing = self.graph.nodes, c.angle

        def penalty(v):
            nx, ny = nodes[v][0], nodes[v][1]
            near = abs(nx - cx) < FLANK_RANGE and abs(ny - cy) < FLANK_RANGE
            return FLANK_PENALTY if near and in_cone(nx, ny, cx, cy, facing) else 0.0
        return penalty

    def _idle_goal(self, t, x, y, tag):
        """With no enemy to look for: fetch a better allowed weapon than any held; then an item-timing bot runs
        its item circuit; any other bot (or a timer with nothing due) keeps its current wander goal until
        GOAL_TIMEOUT, then draws a new one."""
        held_best = max((self.d.weapon_weights.get(w, 0) for w in self.armory.owned if w in self.resolved), default=0.0)
        if held_best < self.best_weight:
            goal, upgrade = self._weapon_goal(t, x, y, better_than=held_best)  # a better weapon first
            if goal is not None:
                return goal, upgrade
        if self.d.item_timing:
            held = self._item_at(self.goal) if self.goal_tag == "item:timed" else None
            if held is not None and pending(held, t) and t - self.goal_set_at < GOAL_TIMEOUT + TIMED_WAIT_MAX:
                return self.goal, "item:timed"  # keep the item being timed until it is back
            goal, timed = self._timed_item_goal(t, x, y)
            if goal is not None:
                return goal, timed
        if self.goal_tag == tag and self.goal is not None and t - self.goal_set_at < GOAL_TIMEOUT:
            return self.goal, tag
        return self._random_attractor(t, x, y), tag

    def _weapon_goal(self, t, x, y, better_than=None):
        """The nearest reachable weapon the bot wants: one its sheet rates at least half its best, or with
        `better_than`, any allowed weapon rated above that."""
        floor = 0.5 * self.best_weight if better_than is None else better_than + 1e-9
        want = [w for w in self.resolved if self.d.weapon_weights.get(w, 0) >= floor and w not in self.armory.owned]
        reach = self._reach(t, x, y)
        best = None
        for name, wx, wy, n in self.weapon_things:
            if name in want and n in reach and (best is None or reach[n] < best[0]):
                best = (reach[n], (wx, wy))
        return (best[1], "weapon") if best else (None, None)

    def _ammo_low(self, v, usable):
        return all(fuzzy.interp(fuzzy.AMMO[w], self.armory.ammo(w, v)) < 0.5 for w in usable if w in fuzzy.AMMO) \
            and any(w in fuzzy.AMMO for w in usable)

    def _item_goal(self, t, x, y, cls=None, names=None, need=100):
        """Best item by gain over path cost. An item-timing bot also weighs items due back by the time it
        arrives, the wait counted as distance."""
        reach = self._reach(t, x, y)
        best = None
        for it in self.items:
            if (cls and it["class"] != cls) or (names and it["name"] not in names) or it["node"] not in reach:
                continue
            wait = self._wait(t, it, reach[it["node"]] / RUN_SPEED)
            if wait is None:
                continue
            gain = min(ITEM_VALUE.get(it["name"], 10), max(1, need)) if cls else 10
            u = gain / (reach[it["node"]] + 200.0 + RUN_SPEED * wait)
            if best is None or u > best[0]:
                best = (u, (it["x"], it["y"]))
        return (best[1], f"item:{cls or 'ammo'}") if best else (None, None)

    def _timed_item_goal(self, t, x, y):
        """Roaming, an item-timing bot heads for the timed item it can reach closest to its respawn,
        weighted by its sheet's item weights; items already standing do not draw it."""
        reach = self._reach(t, x, y)
        best = None
        for it in self.items:
            if it["name"] not in itemfacts.TIMED or it["node"] not in reach or it["back_hi"] <= t:
                continue
            eta = reach[it["node"]] / RUN_SPEED
            wait = self._wait(t, it, eta)
            weight = self.d.item_weights.get(it["class"], 0.0)
            if wait is None or weight <= 0:
                continue
            u = weight * ITEM_VALUE.get(it["name"], 10) / (reach[it["node"]] + 200.0 + RUN_SPEED * wait)
            if best is None or u > best[0]:
                best = (u, (it["x"], it["y"]))
        return (best[1], "item:timed") if best else (None, None)

    def _item_at(self, goal):
        if goal is None:
            return None
        return next((it for it in self.items if it["x"] == goal[0] and it["y"] == goal[1]), None)

    def _lure_goal(self, t, x, y):
        """A berserk pack (LURE_CLASSES) the sheet weights at LURE_WEIGHT or more, while the bot lacks it.
        Arriving where one should be without picking it up shows it is gone: it is back within 30 s."""
        reach = self._reach(t, x, y)
        best = None
        for it in self.items:
            if it["class"] not in LURE_CLASSES or self.d.item_weights.get(it["class"], 0.0) < LURE_WEIGHT:
                continue
            if self.berserk or it["back"] > t or it["node"] not in reach:
                continue
            if math.hypot(it["x"] - x, it["y"] - y) < 12:
                it["back"] = max(it["back"], t + ITEM_RESPAWN // 2)
                it["back_hi"] = max(it["back_hi"], t + ITEM_RESPAWN)
                continue
            if best is None or reach[it["node"]] < best[0]:
                best = (reach[it["node"]], (it["x"], it["y"]))
        return (best[1], "item:lure") if best else (None, None)

    def _suicide_now(self, t, x, y):
        """Trapped in a pocket without a reachable exit switch, nobody in view: respawn by the console suicide."""
        if (self.suicide_sent or self.perc.visible() or t - self.spawned_at <= TRAPPED_WAIT
                or (self.goal_tag or "").startswith("exit:")):
            return False
        if len(self._reach(t, x, y)) >= TRAPPED_NODES or self._exit_goal(t, x, y)[0] is not None:
            return False
        self.suicide_sent = True
        return True

    def _exit_goal(self, t, x, y):
        """In a pocket the bot cannot leave: the approach point of an exit switch it can reach."""
        reach = self._reach(t, x, y)
        if len(reach) >= TRAPPED_NODES:
            return None, None
        for line_id, (ax, ay), _ in self.exits:
            n = self.graph.nearest(ax, ay, max_dist=64)
            if n is not None and n in reach:
                return (ax, ay), f"exit:{line_id}"
        return None, None

    def _enemy_goal(self, t, c):
        """Where to look for a known enemy: where it is expected, or for a flanker a point behind it."""
        cx, cy = c.position_at(t)
        if not self.d.flank or c.angle is None:
            return cx, cy
        a = math.radians(c.angle)
        rx, ry = cx - FLANK_REAR * math.cos(a), cy - FLANK_REAR * math.sin(a)
        n = self.graph.nearest(rx, ry, max_dist=96)
        return (rx, ry) if n is not None and self.graph.move_check((cx, cy), (rx, ry)) is not None else (cx, cy)

    def _random_attractor(self, t, x, y):
        reach = self._reach(t, x, y)
        ok = [p[:2] for p in self.attractors if p[2] in reach and reach[p[2]] > 400]
        return self.rng.choice(ok or [p[:2] for p in self.attractors if p[2] in reach] or [(x, y)])

    def _away_from_threat(self, t, x, y):
        threats = self.perc.recent(t, THREAT_MEMORY)
        if not threats:
            return None
        tx, ty = threats[0].position_at(t)
        reach = self._reach(t, x, y)
        best = None
        for p in self.attractors:
            dd = reach.get(p[2], math.inf)
            if 300 < dd < 1500:
                u = math.hypot(p[0] - tx, p[1] - ty) - 0.5 * dd
                if best is None or u > best[0]:
                    best = (u, p[:2])
        return best[1] if best else None

    def _plan(self, t, x, y):
        self.planned_at = t
        g = self.graph
        start = g.nearest(x, y)
        goal = g.nearest(self.goal[0], self.goal[1], max_dist=256, need_clear=False)
        allow = nav.USABLE_KINDS if t >= self.lift_blocked_until else nav.USABLE_KINDS - {"lift"}
        blocked = {e for e, until in self.blocked.items() if until > t}
        self.path = g.astar(start, goal, live=self.live, allow=allow, blocked=blocked, penalty=self._flank_penalty(t))
        self.path_i = 0
        self.goal_unreachable = self.path is None and g.move_check((x, y), self.goal) is None

    # ------------------------------------------------------------------ execute (every tic)
    def _execute(self, t, v, x, y, z, ang):
        d = self.d
        action = {}
        self.ang = ang
        face, move, run = None, None, True

        aim = None
        if self.engaging and self.target is not None and self.target.seen_for(t) >= d.reaction_tics:
            aim = self._aim(t, v, x, y, z)
            face = aim["yaw"]

        # hold only when the spot is the current goal: fetching a weapon or ammo comes first
        hold_here = (d.spot_mode and self.at_spot and self.behavior in (d.hold_label, "IN_COMBAT")
                     and (self.goal_tag is None or self.goal_tag.startswith("spot:")))
        if self.behavior == "IN_COMBAT" and self.target is not None and not hold_here:
            if self.target.visible or self.path is None:
                move = self._combat_move(t, x, y)
            else:
                move = self._follow(t, x, y, action)
        elif hold_here:
            move = None
            if face is None:
                sweep = 30.0 * math.sin(2 * math.pi * (t - (self.spot_since or t)) / (5 * TICRATE))
                face = self.spot["watch_angle"] + sweep
        elif self._waiting(t, x, y):
            # on the item's place until it respawns: an item that appears under a standing player is
            # picked up only when that player moves, so step side to side
            move, run = ang + (90.0 if (t // 4) % 2 else -90.0), False
            if face is None:
                face = ang
        else:
            move = self._follow(t, x, y, action)
            if move is not None and self.spot is not None and self.goal_tag == f"spot:{self.spot['id']}":
                move, run = self._arrive(x, y, move)
            elif (move is None and self.goal is not None and self.goal_tag in ("weapon", "item:health", "item:armor",
                                                                               "item:ammo", "item:lure")
                  and math.hypot(self.goal[0] - x, self.goal[1] - y) <= ARRIVE):
                # on a pickup that has not been picked up: Doom picks up only on a move, and a player
                # spawned on top of a weapon would otherwise stand on it for good
                move, run = ang + (90.0 if (t // 4) % 2 else -90.0), False
                face = ang if face is None else face

        if t < self.unstick_until:
            move = (ang + 90.0 * self.unstick_dir) if move is None else move + 90.0 * self.unstick_dir
        elif move is not None:
            self._check_stuck(t, x, y)
        else:
            self.trail.clear()  # standing still on purpose is not being stuck

        if self.goal_tag and self.goal_tag.startswith("exit:"):
            line_id = int(self.goal_tag[5:])
            _, (ax, ay), (mx, my) = next(e for e in self.exits if e[0] == line_id)
            if math.hypot(ax - x, ay - y) < 32:  # USE reaches 72 units from the line's midpoint
                move, face = None, bearing(x, y, mx, my)
                if t - self.use_tic >= 8 and self._use_hits(x, y, line_id):
                    action["USE"], self.use_tic = 1, t
        door = action.pop("_door", None)
        if door is not None:  # a closed door on the path: open it before anything else
            face, aim = door, None
        if face is None:
            if t < self.look_back_until:
                face = self.look_back_dir
            elif action.get("_face") is not None:
                face = action["_face"]
            elif move is not None:
                face = move
        action.pop("_face", None)
        turn = 0.0
        if face is not None:
            turn = max(-d.max_turn, min(d.max_turn, wrap180(face - ang)))
            if turn:
                action["TURN_LEFT_RIGHT_DELTA"] = -turn  # positive delta turns clockwise (angle decreases)
        new_ang = ang + turn
        want_pitch = aim["pitch"] if aim else 0.0
        up = -v["PITCH"]  # ZDoom pitch is negative looking up
        dp = max(-d.max_turn, min(d.max_turn, want_pitch - up))
        if abs(dp) > 0.05:
            action["LOOK_UP_DOWN_DELTA"] = dp

        if move is not None:
            rel = math.radians(wrap180(move - new_ang))
            fwd, side = math.cos(rel), math.sin(rel)
            if fwd > 0.38:
                action["MOVE_FORWARD"] = 1
            elif fwd < -0.38:
                action["MOVE_BACKWARD"] = 1
            if side > 0.38:
                action["MOVE_LEFT"] = 1
            elif side < -0.38:
                action["MOVE_RIGHT"] = 1
            if run:
                action["SPEED"] = 1

        if (aim is not None and self.armory.chosen is not None and self.armory.ready(v)
                and fuzzy.in_reach(self.armory.chosen, aim["dist"])):
            err_yaw = abs(wrap180(aim["yaw"] - new_ang))
            err_pitch = abs(want_pitch - (up + dp))
            if err_yaw <= aim["tol"] and err_pitch <= 6.0 and not aim["unsafe"]:
                seen = [(c.x, c.y, c.vx, c.vy) for c in self.perc.visible()]
                if self.armory.chosen == "rocket_launcher" and not self.rocket.clear(
                        x, y, z, new_ang, up + dp, self.heights, seen)[0]:
                    self.rockets_held += 1  # a wall, floor or thing within 160 units of the muzzle
                else:
                    action["ATTACK"] = 1
        return action

    def _aim(self, t, v, x, y, z):
        """Aim point: last seen position, led for projectiles, plus this decision's jitter."""
        c, d = self.target, self.d
        cx, cy = c.position_at(t)
        dist = max(1.0, math.hypot(cx - x, cy - y))
        w = self.armory.chosen
        speed = fuzzy.PROJECTILE_SPEED.get(w)
        if speed and c.visible:
            lead_t = dist / speed * d.lead
            cx, cy = cx + c.vx * lead_t, cy + c.vy * lead_t
        yaw = bearing(x, y, cx, cy) + self.jit_yaw
        pitch = math.degrees(math.atan2((c.z + CHEST) - (z + EYE), dist)) + self.jit_pitch
        tol = math.degrees(math.atan2(18.0, dist)) + fuzzy.SPREAD_DEG.get(w, 1.0)
        unsafe = False  # rocket safety, the target included, is checked at the trigger (ballistics.py)
        return {"yaw": yaw, "pitch": max(-30.0, min(30.0, pitch)), "tol": tol, "unsafe": unsafe, "dist": dist}

    def _arrive(self, x, y, move):
        """Approach the spot without sliding past it: walk when close, counter-thrust when coasting would overshoot."""
        sx, sy = self.spot["x"], self.spot["y"]
        dist = math.hypot(sx - x, sy - y)
        if dist > 256 or len(self.trail) < 2:
            return move, True
        if self.graph.move_check((x, y), (sx, sy)) is None:
            return move, True  # not straight-line reachable (a ledge, a wall): keep following the path
        (px, py), (cx, cy) = self.trail[-2], self.trail[-1]
        toward = ((cx - px) * (sx - x) + (cy - py) * (sy - y)) / max(dist, 1.0)
        if toward * FRICTION_SLIDE > dist:
            return bearing(x, y, sx, sy) + 180.0, False
        return bearing(x, y, sx, sy), dist > 128

    def _waiting(self, t, x, y):
        it = self.wait_item
        return (it is not None and self.behavior != "IN_COMBAT" and pending(it, t)
                and math.hypot(it["x"] - x, it["y"] - y) < WAIT_RADIUS)

    def _combat_move(self, t, x, y):
        """Close to the dial-set range (aggression pushes toward rmin), strafing per aggression.
        Inside the retreat band, back straight off on a clear heading. A flanker inside its target's
        view cone strafes toward the cone's nearer edge instead of at random."""
        d = self.d
        cx, cy = self.target.position_at(t)
        dist = math.hypot(cx - x, cy - y)
        to = bearing(x, y, cx, cy)
        if d.retreat_distance is not None and dist < d.retreat_distance:
            away = self._clear_heading(x, y, to + 180.0)
            if away is not None:
                return away
        fx = fy = 0.0
        if dist > d.desired_range + 32:
            fx, fy = math.cos(math.radians(to)), math.sin(math.radians(to))
        elif dist < d.desired_range - 32:
            fx, fy = -math.cos(math.radians(to)), -math.sin(math.radians(to))
        strafe = self.strafe
        c = self.target
        if d.flank and c.angle is not None and in_cone(x, y, cx, cy, c.angle):
            # circle out of the cone the short way: counter-clockwise round the target (heading to - 90,
            # strafe -1) when the bot is on its left, clockwise when on its right
            strafe = -1 if wrap180(bearing(cx, cy, x, y) - c.angle) >= 0 else 1
        if strafe:
            s = math.radians(to + 90.0 * strafe)
            fx, fy = fx + math.cos(s), fy + math.sin(s)
        if abs(fx) < 1e-6 and abs(fy) < 1e-6:
            return None
        return math.degrees(math.atan2(fy, fx))

    def _clear_heading(self, x, y, want):
        """The heading nearest `want` (within 90 degrees) along which the bot can walk RETREAT_PROBE units."""
        for off in (0, 30, -30, 60, -60, 90, -90):
            h = math.radians(want + off)
            if self.graph.move_check((x, y), (x + RETREAT_PROBE * math.cos(h), y + RETREAT_PROBE * math.sin(h))) is not None:
                return want + off
        return None

    def _follow(self, t, x, y, action):
        """Heading along the planned path; handles doors (USE), lifts (wait, USE) and teleporters."""
        g = self.graph
        if self.goal is None:
            return None
        if not self.path:
            # no route: head straight for the goal only if that is walkable from here
            if math.hypot(self.goal[0] - x, self.goal[1] - y) > ARRIVE and not self.goal_unreachable:
                return bearing(x, y, self.goal[0], self.goal[1])
            return None
        while self.path_i < len(self.path):
            nx, ny, _ = g.nodes[self.path[self.path_i]]
            if math.hypot(nx - x, ny - y) < ARRIVE:
                self.path_i += 1
            else:
                break
        if self.path_i >= len(self.path):
            if math.hypot(self.goal[0] - x, self.goal[1] - y) > ARRIVE:
                return bearing(x, y, self.goal[0], self.goal[1])
            return None
        u = self.path[self.path_i - 1] if self.path_i > 0 else None
        vtx = self.path[self.path_i]
        kind, info = g.edge(u, vtx) if u is not None else ("walk", None)
        if kind != "teleport" and math.hypot(g.nodes[vtx][0] - x, g.nodes[vtx][1] - y) > 256:
            self._plan(t, x, y)  # knocked off the path, or just teleported
            if not self.path:
                return None
            return bearing(x, y, g.nodes[self.path[0]][0], g.nodes[self.path[0]][1])
        tx, ty = g.nodes[vtx][0], g.nodes[vtx][1]
        if kind == "teleport":
            tl = g.teleport_lines[info]
            (mx, my), (nx_, ny_) = tl["mid"], tl["normal"]
            return bearing(x, y, mx - 24 * nx_, my - 24 * ny_)
        if kind == "door":
            ln = g.lines[info]
            mx, my = (ln.a[0] + ln.b[0]) / 2, (ln.a[1] + ln.b[1]) / 2
            door = ln.back if ln.back in g.manual_doors else ln.front
            closed = self.door_ceiling.get(door, g.ceil[door]) - g.floor[door] < 56
            if closed and math.hypot(mx - x, my - y) < 96:
                action["_door"] = bearing(x, y, mx, my)
                if t - self.use_tic >= 8 and self._use_hits(x, y, info):
                    action["USE"], self.use_tic = 1, t
        if kind == "lift":
            h = self._lift_move(t, x, y, u, vtx, info, action)
            if h is not False:
                return h
        else:
            self.lift_wait_since = None
            for k in (2, 1):  # look ahead along straight walkable stretches
                j = self.path_i + k
                if j < len(self.path) and all(g.edge(self.path[i - 1], self.path[i])[0] == "walk"
                                              for i in range(self.path_i, j + 1) if i > 0):
                    p = g.nodes[self.path[j]]
                    if t % 4 == 0 and g.move_check((x, y), (p[0], p[1])) == ("walk", None):
                        self.path_i = j
                        tx, ty = p[0], p[1]
                        break
        return bearing(x, y, tx, ty)

    def _lift_move(self, t, x, y, u, vtx, lift, action):
        """Step on or off a lift when its live floor allows; otherwise press its switch or wait."""
        g = self.graph
        s_from, s_to = g.nodes[u][2], g.nodes[vtx][2]
        f_from = self.lift_floor.get(s_from, g.floor[s_from])
        f_to = self.lift_floor.get(s_to, g.floor[s_to])
        if f_to - f_from <= 24:
            self.lift_wait_since = None
            return False  # walk on
        if self.lift_wait_since is None:
            self.lift_wait_since = t
        if t - self.lift_wait_since > LIFT_GIVE_UP:
            self.lift_blocked_until, self.lift_wait_since = t + 20 * TICRATE, None
            self._plan(t, x, y)
            return None
        for lid in g.lift_switch_lines:
            ln = g.lines[lid]
            mx, my = (ln.a[0] + ln.b[0]) / 2, (ln.a[1] + ln.b[1]) / 2
            if math.hypot(mx - x, my - y) < 96:
                action["_face"] = bearing(x, y, mx, my)
                if t - self.use_tic >= 8 and self._use_hits(x, y, lid):
                    action["USE"], self.use_tic = 1, t
                return None
        return None  # wait for the platform

    def _use_hits(self, x, y, line_id):
        """USE only when the first special line within reach along the current facing is the intended
        one. That keeps the bot off the exit switch, which kills the player under sv_noexit."""
        g = self.graph
        ln = g.lines[line_id]
        mx, my = (ln.a[0] + ln.b[0]) / 2, (ln.a[1] + ln.b[1]) / 2
        if math.hypot(mx - x, my - y) > 72 or abs(wrap180(bearing(x, y, mx, my) - self.ang)) > 25:
            return False
        a = math.radians(self.ang)
        p, q = (x, y), (x + 72 * math.cos(a), y + 72 * math.sin(a))
        hits = []
        for other in g.lines_near(min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]), 4):
            if other.special and nav.segments_cross(p, q, other.a, other.b):
                hits.append((math.hypot(((other.a[0] + other.b[0]) / 2) - x, ((other.a[1] + other.b[1]) / 2) - y), other.id))
        return bool(hits) and min(hits)[1] == line_id

    def _check_stuck(self, t, x, y):
        if len(self.trail) >= 24 and math.dist(self.trail[0], self.trail[-1]) < 12:
            self.stuck_count += 1
            if self.path and 0 < self.path_i < len(self.path):
                # something the graph does not know about (a player in a doorway, a thing) is in the way
                self.blocked[(self.path[self.path_i - 1], self.path[self.path_i])] = t + BLOCK_TICS
            self.unstick_until, self.unstick_dir = t + 10, self.rng.choice((-1, 1))
            self.trail.clear()
            if self.goal is not None:
                self._plan(t, x, y)
            if self.stuck_count >= 3:
                self.goal, self.goal_tag, self.path, self.stuck_count = None, None, None, 0
