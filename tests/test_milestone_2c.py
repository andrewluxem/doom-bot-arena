"""Milestone 2C: observed traffic for spots, PYR's engage_only, ORB's KITING label, third-party kills."""

import json
import math
import types

from engine import roster
from engine.personality import controller as C
from engine.personality import perception as P
from engine.personality import traffic
from tools import signatures as S

SHEETS = {sh["code"]: sh for sh in roster.load()}


# ---------------------------------------------------------------- traffic map
def write_bundle(path, rows_by_slot, f0=0, map_name="MAP10", sha="abc", events=()):
    end = len(next(iter(rows_by_slot.values())))
    bots = [{"slot": s, "code": c, "controller": "engine.personality.controller:PersonalityBot"}
            for s, c in zip(rows_by_slot, ("PYR", "VEX", "KRN", "NUL"))]
    path.mkdir(parents=True, exist_ok=True)
    (path / "match.json").write_text(json.dumps({"id": path.name, "map": map_name, "iwad": {"sha256": sha},
                                                 "frame0_tic": f0, "duration_tics": end - f0, "seed": 1, "bots": bots}))
    with open(path / "tics.jsonl", "w") as f:
        for s, rows in rows_by_slot.items():
            for t, r in enumerate(rows):
                f.write(json.dumps({"tic": t, "slot": s, "target_slot": None, **r}) + "\n")
    with open(path / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def test_traffic_counts_alive_displayed_rows_of_the_same_map_only(tmp_path):
    rows = [{"x": 10.0, "y": 10.0, "alive": True}] * 2 + [{"x": 70.0, "y": -5.0, "alive": True},
                                                          {"x": 70.0, "y": -5.0, "alive": False}]
    write_bundle(tmp_path / "b" / "one", {1: rows}, f0=1)
    write_bundle(tmp_path / "b" / "other", {1: rows}, map_name="E1M5")
    md = {"source": {"map": "MAP10", "iwad_sha256": "abc"}}
    tr = traffic.build([str(tmp_path / "b")], md)
    assert tr["bundles"] == ["one"] and tr["samples"] == 2  # tic 0 is pre-roll, tic 3 dead
    assert tr["cells"] == {"0,0": 1, "1,-1": 1}
    g = traffic.Grid(tr)
    assert g.around(0.0, 0.0) == 2 and g.around(200.0, 0.0) == 0
    assert traffic.build([str(tmp_path / "nothing")], md) is None
    assert traffic.digest(None) == "routes" and len(traffic.digest(tr)) == 12


# ---------------------------------------------------------------- engage_only (PYR)
def contact(x, y, angle):
    c = P.Contact(1, None, 0)
    c.x, c.y, c.angle, c.visible = x, y, angle, True
    return c


def label(name, x, y):
    return types.SimpleNamespace(object_name=name, object_position_x=x, object_position_y=y)


def test_busy_is_aiming_at_another_player_and_hurt_is_blood_nearby():
    perc = P.Perception(7)
    a = contact(500.0, 0.0, 90.0)  # faces north, at b
    b = contact(500.0, 400.0, 270.0)  # faces south, at a
    c = contact(800.0, 0.0, 180.0)  # faces west, at the bot at the origin (and past a)
    perc.contacts = {1: a, 2: b, 3: c}
    perc._busy_and_hurt(10, types.SimpleNamespace(labels=[label("Blood", 520.0, 410.0)]), 0.0, 0.0)
    assert a.busy_at == 10 and b.busy_at == 10
    assert c.busy_at < 0  # faces the bot more squarely than a
    assert b.hurt_at == 10 and a.hurt_at < 0


def test_pyr_fights_only_busy_or_hurt_enemies():
    d = C.Dials(SHEETS["PYR"])
    assert d.engage_only and d.engage_busy and d.hurt_window == 105
    assert not C.Dials(SHEETS["VEX"]).engage_only
    b = C.PersonalityBot.__new__(C.PersonalityBot)
    b.d = d
    c = contact(0.0, 0.0, 0.0)
    assert not b._fair_game(c, 100)
    c.busy_at = 100 - C.BUSY_MEMORY
    assert b._fair_game(c, 100) and not b._fair_game(c, 101)
    c.busy_at, c.hurt_at = -10 ** 9, 0
    assert b._fair_game(c, 105) and not b._fair_game(c, 106)


# ---------------------------------------------------------------- KITING (ORB)
def test_kiting_is_in_the_vocabulary_but_never_chosen_by_utility():
    assert "KITING" in roster.BEHAVIORS and roster.readme_behaviors() == roster.BEHAVIORS
    from engine.personality import utility
    assert "KITING" not in utility.BEHAVIORS


# ---------------------------------------------------------------- third-party kills
def row(x, alive=True, tgt=None, taken=0, dealt=0):
    return {"x": x, "y": 0.0, "angle": 0.0, "alive": alive, "target_slot": tgt, "damage_taken": taken,
            "damagecount": dealt, "health": 100, "armor": 0, "behavior": "ROAMING", "hitcount": 0}


def test_third_party_kill_share(tmp_path):
    n = 300
    pyr = [row(0.0) for _ in range(n)]
    vex = [row(100.0, tgt=3 if 40 <= t < 60 else None) for t in range(n)]  # VEX fights KRN until tic 59
    krn = [row(200.0, taken=0 if t < 150 else 10, dealt=0) for t in range(n)]
    nul = [row(300.0, dealt=0 if t < 150 else 10) for t in range(n)]  # NUL hurts KRN at tic 150
    events = [{"tic": 100, "type": "frag", "actor": 1, "target": 2, "credited": True},  # VEX busy until 59: > 35 tics before
              {"tic": 80, "type": "frag", "actor": 1, "target": 2, "credited": True},   # 21 tics after: counts
              {"tic": 200, "type": "frag", "actor": 1, "target": 3, "credited": True}]  # KRN hurt by NUL 50 tics before
    B = S.Bundle(str(write_bundle(tmp_path / "m", {1: pyr, 2: vex, 3: krn, 4: nul}, events=events)))
    assert math.isclose(S.third_party_kill_share(B, 1, {}), 2 / 3)
    assert S.third_party_kill_share(B, 2, {}) is None  # no kills
