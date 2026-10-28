"""Milestone 2B engine mechanics, each driven by a sheet field (characters/README.md)."""

import math
import random

from engine import roster
from engine.personality import controller as C
from engine.personality import fuzzy, nav

SHEETS = {sh["code"]: sh for sh in roster.load()}


# ---------------------------------------------------------------- re-roll (ZED)
def test_reroll_is_seeded_by_match_slot_and_epoch():
    zed = SHEETS["ZED"]
    a = C.roll(zed, random.Random("301:8:reroll:3"))
    b = C.roll(zed, random.Random("301:8:reroll:3"))
    c = C.roll(zed, random.Random("301:8:reroll:4"))
    assert a == b and a != c
    assert set(a) == set(zed["ranges"])
    for path, (lo, hi) in zed["ranges"].items():
        assert lo <= a[path] <= hi


def test_rolled_values_reach_the_dials_and_leave_the_sheet_alone():
    zed = SHEETS["ZED"]
    values = {"temperament.aggression": 0.25, "behavior_weights.FLEEING": 0.6}
    d = C.Dials(C.with_values(zed, values))
    assert d.aggression == 0.25 and d.behavior_weights["FLEEING"] == 0.6
    assert d.desired_range == 0 + 400 * 0.75  # derived dials follow
    assert zed["temperament"]["aggression"] == 0.7


def test_only_zed_rerolls():
    for code, sh in SHEETS.items():
        assert bool(sh.get("ranges")) == (code == "ZED")


# ---------------------------------------------------------------- item timing (KRN)
def item():
    return {"back": 0, "back_hi": 0, "seen": None}


def test_item_seen_to_vanish_gets_a_window():
    it = item()
    C.observe_item(it, 100, True)
    C.observe_item(it, 400, False)  # taken between tic 100 and 400
    assert (it["back"], it["back_hi"]) == (100 + C.ITEM_RESPAWN, 400 + C.ITEM_RESPAWN)
    C.observe_item(it, 500, False)  # still gone: the window stands
    assert (it["back"], it["back_hi"]) == (100 + C.ITEM_RESPAWN, 400 + C.ITEM_RESPAWN)
    C.observe_item(it, 1300, True)  # back early in its window
    assert (it["back"], it["back_hi"], it["seen"]) == (1300, 1300, 1300)


def test_a_late_item_keeps_its_timer_through_the_grace():
    it = item()
    it["back"] = it["back_hi"] = 1100  # own pickup at tic 50
    assert C.pending(it, 1100) and C.pending(it, 1100 + C.WAIT_GRACE - 1)
    C.observe_item(it, 1102, False)  # two tics late: not a new pickup
    assert (it["back"], it["back_hi"]) == (1102, 1100)
    C.observe_item(it, 1104, True)
    assert not C.pending(it, 1104) and it["seen"] == 1104


def test_item_never_seen_then_missing_can_be_back_any_time_within_30s():
    it = item()
    C.observe_item(it, 700, False)
    assert (it["back"], it["back_hi"]) == (700, 700 + C.ITEM_RESPAWN)
    C.observe_item(it, 900, False)
    assert (it["back"], it["back_hi"]) == (900, 700 + C.ITEM_RESPAWN)
    assert C.expected_back(it) == (900 + 700 + C.ITEM_RESPAWN) / 2
    C.observe_item(it, 700 + C.ITEM_RESPAWN + C.WAIT_GRACE + 1, False)  # overdue: taken again unseen
    assert it["back_hi"] == 700 + 2 * C.ITEM_RESPAWN + C.WAIT_GRACE + 1


# ---------------------------------------------------------------- flank (PYR)
def test_view_cone():
    assert C.in_cone(100, 10, 0, 0, 0.0)  # straight ahead
    assert not C.in_cone(-100, 0, 0, 0, 0.0)  # behind
    assert not C.in_cone(0, 100, 0, 0, 0.0)  # beside, 90 degrees off
    assert C.in_cone(0, 100, 0, 0, 80.0)


def line_graph():
    """Two routes from node 0 to node 3: through node 1 (shorter) or node 2."""
    g = nav.NavGraph.__new__(nav.NavGraph)
    g.nodes = [(0, 0, 0), (100, 10, 0), (100, -60, 0), (200, 0, 0)]
    e = lambda a, b: (b, math.dist(g.nodes[a][:2], g.nodes[b][:2]), "walk", None)
    g.edges = [[e(0, 1), e(0, 2)], [e(1, 3)], [e(2, 3)], []]
    return g


def test_astar_penalty_moves_the_path():
    g = line_graph()
    assert g.astar(0, 3) == [0, 1, 3]
    assert g.astar(0, 3, penalty=lambda v: 3.0 if v == 1 else 0.0) == [0, 2, 3]


class Graph:
    def __init__(self, walls=()):
        self.walls = walls

    def move_check(self, p, q):
        return None if any(w(q) for w in self.walls) else ("walk", None)


def bot(code, target_xy, target_angle=None, walls=()):
    b = C.PersonalityBot.__new__(C.PersonalityBot)
    b.d = C.Dials(SHEETS[code])
    b.graph = Graph(walls)
    b.strafe = 0
    t = type("Contact", (), {})()
    t.angle = target_angle
    t.position_at = lambda tic: target_xy
    b.target = t
    return b


def test_retreat_band_backs_off_on_a_clear_heading():
    orb = bot("ORB", (200.0, 0.0))  # 200 units east, inside retreat_distance 350
    assert C.wrap180(orb._combat_move(0, 0.0, 0.0) - 180.0) == 0  # straight west
    walled = bot("ORB", (200.0, 0.0), walls=[lambda q: q[0] < -50 and abs(q[1]) < 60])
    h = walled._combat_move(0, 0.0, 0.0)
    assert 90 <= abs(C.wrap180(h)) < 180  # away from the target, round the wall
    assert bot("ORB", (560.0, 0.0))._combat_move(0, 0.0, 0.0) is None  # at its desired range (560): holds


def test_flanker_strafes_out_of_the_cone_the_short_way():
    # target at (300, 0) facing west (180) toward the bot at the origin, slightly to the target's left
    pyr = bot("PYR", (300.0, 0.0), target_angle=170.0)
    h = pyr._combat_move(0, 0.0, 0.0)
    nx, ny = math.cos(math.radians(h)), math.sin(math.radians(h))
    before = abs(C.wrap180(C.bearing(300, 0, 0, 0) - 170.0))
    after = abs(C.wrap180(C.bearing(300, 0, 10 * nx, 10 * ny) - 170.0))
    assert after > before
    no_flank = bot("VEX", (300.0, 0.0), target_angle=170.0)
    assert C.wrap180(no_flank._combat_move(0, 0.0, 0.0)) == 0  # VEX just closes


# ---------------------------------------------------------------- melee and berserk (ASH)
def test_melee_fires_only_in_reach_and_berserk_prefers_the_fist():
    assert fuzzy.in_reach("chainsaw", 60) and not fuzzy.in_reach("chainsaw", 120)
    assert fuzzy.in_reach("fist", 70) and not fuzzy.in_reach("fist", 90)
    ash = SHEETS["ASH"]["weapons"]["weights"]
    inf = lambda w: math.inf
    assert fuzzy.choose(["fist", "chainsaw"], ash, 40, inf)[0] == "chainsaw"
    assert fuzzy.choose(["fist", "chainsaw"], ash, 40, inf, berserk=True)[0] == "fist"


def test_idle_bots_fetch_a_better_weapon_than_any_held():
    b = C.PersonalityBot.__new__(C.PersonalityBot)
    b.d = C.Dials(SHEETS["ASH"])
    b.resolved, b.best_weight = ["chainsaw", "fist"], 1.0
    b.armory = type("Armory", (), {"owned": {"fist", "pistol"}})()
    b.weapon_things = [("chainsaw", 500.0, 0.0, 7), ("shotgun", 100.0, 0.0, 8)]
    b._reach = lambda t, x, y: {7: 600.0, 8: 120.0}
    b.goal = b.goal_tag = None
    assert b._idle_goal(0, 0.0, 0.0, "roam") == ((500.0, 0.0), "weapon")  # fist 0.5 held, chainsaw 1.0 wanted
    b.armory.owned.add("chainsaw")
    b.attractors, b.rng = [(9.0, 9.0, 8)], random.Random(1)
    b.d.item_timing = False
    assert b._idle_goal(0, 0.0, 0.0, "roam")[1] == "roam"  # nothing better to fetch
