"""Personality bot: one engine for every character, driven only by its sheet's dials.

Per tic it perceives (labels buffer only), aims and moves. At 10 Hz (every 3 or 4 tics)
it re-scores the behaviors (utility.py), picks a target and a weapon (fuzzy.py), and
re-plans its goal on the nav graph (nav.py). Aim is limited by the three perception
dials: reaction_time_ms before acting on a new sighting, aim_accuracy as angular jitter
resampled each decision, and max_turn_deg_per_tic on every turn and pitch change.

The RNG is seeded from (match seed, slot).
"""

import json
import math
import os
import random

from engine import weapons
from engine.peers import NoPeers
from engine.personality import fuzzy, nav, utility
from engine.personality import spots as spotlib
from engine.personality.perception import Perception, distance

TICRATE = 35
COMBAT_MEMORY = 21  # tics an enemy stays the target after leaving view
THREAT_MEMORY = 35
RECENT_CONTACT = 6 * TICRATE
JITTER_DEG = 10.0  # aim error standard deviation at aim_accuracy 0
EYE = 41.0
CHEST = 32.0
ITEM_RESPAWN = 30 * TICRATE  # ZDoom deathmatch with sv_itemrespawn
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


class PersonalityBot:
    def __init__(self, slot, sheet, seed, map_data, cache_dir=None):
        self.slot, self.code = slot, sheet["code"]
        self.d = Dials(sheet)
        self.rng = random.Random(f"{seed}:{slot}")
        self.peers = NoPeers()
        self.graph = nav.load(map_data, cache_dir)
        self.resolved = weapons.resolve(self.d.allowed, self.d.weapon_weights, map_data["weapons_present"])
        self.best_weight = max((self.d.weapon_weights.get(w, 0.0) for w in self.resolved), default=0.0)
        self.armory = weapons.Armory(map_data["weapons"])
        self.perc = Perception(slot)
        self.spot_cands = []
        if self.d.spot_mode:
            req = [(self.d.spot_mode, self.d.rmin, self.d.rmax)]
            self.spot_cands = spotlib.table(map_data, cache_dir, req, self.graph)["candidates"][self.d.spot_mode]
        node = lambda x, y: self.graph.nearest(x, y, max_dist=128, need_clear=False)
        self.weapon_things = [(w["name"], w["x"], w["y"], node(w["x"], w["y"]))
                              for w in map_data["weapons"] if w["in_deathmatch"]]
        self.items = [{"name": i["name"], "class": i["class"], "x": i["x"], "y": i["y"], "back": 0,
                       "node": node(i["x"], i["y"])} for i in map_data.get("items", []) if i["in_deathmatch"]]
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
        self.live, self.lift_floor, self.door_ceiling = {}, {}, {}
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
        self.perc.update(t, st, self.peers, x, y)
        self._watch_self(t, v, x, y)
        if (t * 10) // TICRATE != ((t - 1) * 10) // TICRATE or self.scores is None:
            self._read_live(st)
            self._decide(t, v, x, y)
        cmds = self.armory.select(t, self.weapon_choice, v)
        action = self._execute(t, v, x, y, z, ang)
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

    # ------------------------------------------------------------------ lifecycle
    def _on_spawn(self, t):
        self.perc.reset()
        self.target, self.engaging, self.weapon_choice = None, False, None
        self.goal, self.path, self.spot, self.at_spot = None, None, None, False
        self.prev, self.trail, self.stuck_count = None, [], 0
        self.scores = None

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
            best[1]["back"] = t + ITEM_RESPAWN

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
                                 lambda w: self.armory.ammo(w, v), current=self.weapon_choice)
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
                "spot": None if self.spot is None else [self.spot["id"], self.at_spot], "stuck": self.stuck_count})

        self.jit_yaw = self.rng.gauss(0.0, d.jitter_deg)
        self.jit_pitch = self.rng.gauss(0.0, d.jitter_deg * 0.5)
        if self.rng.random() < 0.08 + 0.17 * d.aggression:
            self.strafe = self.rng.choice((-1, 1)) if self.rng.random() < d.aggression else 0

    def _offer(self, t, x, y, cls, need):
        """0..1: value of the best available item of a class, discounted by straight-line distance."""
        best = 0.0
        for it in self.items:
            if it["class"] != cls or it["back"] > t:
                continue
            gain = min(ITEM_VALUE.get(it["name"], 10), max(0, need))
            best = max(best, min(1.0, gain / 25.0) * math.exp(-math.hypot(it["x"] - x, it["y"] - y) / 1500.0))
        return best

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
        if b not in ("IN_COMBAT", "FLEEING") and not armed:
            goal, tag = self._weapon_goal(t, x, y)
        if goal is None and b not in ("IN_COMBAT", "FLEEING") and usable and self._ammo_low(v, usable):
            goal, tag = self._item_goal(t, x, y, names=set(sum((AMMO_ITEMS.get(w, ()) for w in usable), ())) | {"Backpack"})
        if goal is None:
            if b == "IN_COMBAT":
                if self.target is not None and not self.target.visible:
                    goal, tag = self.target.position_at(t), "last_seen"
            elif b == "HUNTING":
                if (latest is not None and t - latest.last_seen <= RECENT_CONTACT
                        and self.searched.get(latest.key) != latest.last_seen):
                    goal, tag = latest.position_at(t), f"hunt:{latest.key}"
                    if math.hypot(goal[0] - x, goal[1] - y) < 64 or (self.goal_tag == tag and self.goal_unreachable):
                        # got there and nobody is in view, or there is no way there (seen through a window)
                        self.searched[latest.key] = latest.last_seen
                        goal, tag = self._random_attractor(t, x, y), "hunt"
                elif self.goal_tag == "hunt" and self.goal is not None and t - self.goal_set_at < GOAL_TIMEOUT:
                    goal, tag = self.goal, "hunt"
                else:
                    goal, tag = self._random_attractor(t, x, y), "hunt"
            elif b == "ROAMING":
                if self.goal_tag == "roam" and self.goal is not None and t - self.goal_set_at < GOAL_TIMEOUT:
                    goal, tag = self.goal, "roam"
                else:
                    goal, tag = self._random_attractor(t, x, y), "roam"
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
        if goal is None:
            self.goal, self.goal_tag, self.path = None, None, None
            return
        if tag != self.goal_tag or self.goal is None or math.hypot(goal[0] - self.goal[0], goal[1] - self.goal[1]) > 96:
            self.goal, self.goal_tag, self.goal_set_at = goal, tag, t
            self._plan(t, x, y)
        elif t - self.planned_at > REPLAN_TICS:
            self._plan(t, x, y)

    def _weapon_goal(self, t, x, y):
        want = [w for w in self.resolved if self.d.weapon_weights.get(w, 0) >= 0.5 * self.best_weight
                and w not in self.armory.owned]
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
        reach = self._reach(t, x, y)
        best = None
        for it in self.items:
            if it["back"] > t or (cls and it["class"] != cls) or (names and it["name"] not in names):
                continue
            if it["node"] not in reach:
                continue
            gain = min(ITEM_VALUE.get(it["name"], 10), max(1, need)) if cls else 10
            u = gain / (reach[it["node"]] + 200.0)
            if best is None or u > best[0]:
                best = (u, (it["x"], it["y"]))
        return (best[1], f"item:{cls or 'ammo'}") if best else (None, None)

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
        self.path = g.astar(start, goal, live=self.live, allow=allow, blocked=blocked)
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
        else:
            move = self._follow(t, x, y, action)
            if move is not None and self.spot is not None and self.goal_tag == f"spot:{self.spot['id']}":
                move, run = self._arrive(x, y, move)

        if t < self.unstick_until:
            move = (ang + 90.0 * self.unstick_dir) if move is None else move + 90.0 * self.unstick_dir
        elif move is not None:
            self._check_stuck(t, x, y)
        else:
            self.trail.clear()  # standing still on purpose is not being stuck

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

        if aim is not None and self.armory.chosen is not None and self.armory.ready(v):
            err_yaw = abs(wrap180(aim["yaw"] - new_ang))
            err_pitch = abs(want_pitch - (up + dp))
            if err_yaw <= aim["tol"] and err_pitch <= 6.0 and not aim["unsafe"]:
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
        unsafe = w == "rocket_launcher" and dist < 144  # inside its own splash
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

    def _combat_move(self, t, x, y):
        """Close to the dial-set range (aggression pushes toward rmin), strafing per aggression."""
        d = self.d
        cx, cy = self.target.position_at(t)
        dist = math.hypot(cx - x, cy - y)
        to = bearing(x, y, cx, cy)
        fx = fy = 0.0
        if dist > d.desired_range + 32:
            fx, fy = math.cos(math.radians(to)), math.sin(math.radians(to))
        elif dist < d.desired_range - 32:
            fx, fy = -math.cos(math.radians(to)), -math.sin(math.radians(to))
        if self.strafe:
            s = math.radians(to + 90.0 * self.strafe)
            fx, fy = fx + math.cos(s), fy + math.sin(s)
        if abs(fx) < 1e-6 and abs(fy) < 1e-6:
            return None
        return math.degrees(math.atan2(fy, fx))

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
