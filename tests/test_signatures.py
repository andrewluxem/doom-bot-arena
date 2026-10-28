"""Signature report: kills_from_hold_share and the separation rule (docs/SIGNATURES.md)."""

import json
import types

import pytest

from tools import signatures as S


def write_bundle(path, rows_by_slot, events, f0=0):
    """A two-bot bundle: slot 3 (NUL, personality) and slot 2 (KRN, placeholder)."""
    end = len(next(iter(rows_by_slot.values())))
    bots = [{"slot": 3, "code": "NUL", "controller": "engine.personality.controller:PersonalityBot"},
            {"slot": 2, "code": "KRN", "controller": S.PLACEHOLDER}]
    (path / "match.json").write_text(json.dumps({"frame0_tic": f0, "duration_tics": end - f0, "seed": 1, "bots": bots}))
    with open(path / "tics.jsonl", "w") as f:
        for s, rows in rows_by_slot.items():
            for t, (x, y, alive) in enumerate(rows):
                f.write(json.dumps({"tic": t, "slot": s, "x": x, "y": y, "alive": alive, "target_slot": None}) + "\n")
    with open(path / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return S.Bundle(str(path))


def kill(t, actor=3, target=2):
    return {"tic": t, "type": "frag", "actor": actor, "target": target, "credited": True}


def test_kills_from_hold_share(tmp_path):
    nul = []
    for t in range(400):
        if t < 200:
            nul.append((100.0 + (t % 3) * 4, 50.0, True))  # holds, shuffling within 8 units
        elif t < 240:
            nul.append((100.0 + (t - 199) * 10, 50.0, True))  # walks 400 units
        elif t < 300:
            nul.append((500.0, 50.0, True))
        elif t < 330:
            nul.append((500.0, 50.0, False))  # dead
        else:
            nul.append((900.0, 50.0, True))  # respawned, standing still
    krn = [(0.0, 0.0, True)] * 400
    B = write_bundle(tmp_path, {3: nul, 2: krn},
                     [kill(150),   # held 150 tics: counts
                      kill(260),   # 20 tics after a 400-unit walk: does not
                      kill(380),   # still for 50 tics since respawn, under 70: does not
                      kill(399)])  # still for 69 tics since respawn: does not
    assert S.kills_from_hold_share(B, 3, {}) == 0.25
    assert S.kills_from_hold_share(B, 2, {}) is None  # no kills


def test_hold_window_needs_70_tics_within_16_units(tmp_path):
    still = [(0.0, 0.0, True)] * 200
    drift = [(0.0, 0.0, True)] * 100 + [(17.0, 0.0, True)] + [(0.0, 0.0, True)] * 99
    B = write_bundle(tmp_path, {3: drift, 2: still}, [kill(160), kill(171)])
    # tic 100 is 17 units off: inside the 70 tics before tic 160, outside those before tic 171
    assert S.kills_from_hold_share(B, 3, {}) == 0.5


def fake_bundles(values):
    """values: one dict per match, slot -> metric value. Slot 1 is VEX; 2, 4, 5, 7, 8 placeholders; 6 HEX."""
    codes = {1: "VEX", 2: "KRN", 4: "ASH", 5: "ORB", 6: "HEX", 7: "PYR", 8: "ZED"}
    out = []
    for v in values:
        out.append(types.SimpleNamespace(
            slots=list(codes), code=codes, values=v,
            controller={s: S.PLACEHOLDER if s in (2, 4, 5, 7, 8) else "personality" for s in codes}))
    return out


@pytest.fixture
def fake_metric(monkeypatch):
    monkeypatch.setitem(S.METRICS, "fake", lambda B, s, sheet: B.values.get(s))


def sheets(gate=True):
    sig = [{"metric": "fake", "op": "<", "value": 10, "gate": gate}]
    return [{"code": c, "signature": sig if c == "VEX" else []} for c in ("VEX", "KRN", "ASH", "ORB", "HEX", "PYR", "ZED")]


def match(vex, placeholders, hex_=0):
    return {1: vex, 6: hex_, **dict(zip((2, 4, 5, 7, 8), placeholders))}


def test_separation_counts_only_placeholders_and_needs_80_percent_failing(fake_metric):
    # HEX (a personality) passes in every match; it must not count as a placeholder
    one_pass = fake_bundles([match(5, [5, 20, 20, 20, 20]), match(5, [20] * 5)])
    [x] = S.separation(one_pass, sheets(), ["VEX"])
    assert (x["placeholders"]["passes"], x["placeholders"]["computed"]) == (1, 10)
    assert x["separates"] and x["can_gate"]  # 90% fail

    two_of_ten = fake_bundles([match(5, [5, 5, 20, 20, 20]), match(5, [20] * 5)])
    [x] = S.separation(two_of_ten, sheets(), ["VEX"])
    assert x["separates"]  # exactly 80% fail

    three_of_ten = fake_bundles([match(5, [5, 5, 5, 20, 20]), match(5, [20] * 5)])
    [x] = S.separation(three_of_ten, sheets(), ["VEX"])
    assert not x["separates"] and not x["can_gate"]  # 70% fail


def test_na_placeholders_are_left_out_not_counted_as_failing(fake_metric):
    bundles = fake_bundles([match(5, [5, None, None, None, None])])
    [x] = S.separation(bundles, sheets(), ["VEX"])
    assert (x["placeholders"]["computed"], x["placeholders"]["na"], x["placeholders"]["passes"]) == (1, 4, 1)
    assert not x["separates"]


def test_a_metric_its_personality_fails_cannot_gate(fake_metric):
    bundles = fake_bundles([match(50, [20] * 5), match(50, [20] * 5)])
    [x] = S.separation(bundles, sheets(), ["VEX"])
    assert x["separates"] and not x["can_gate"]


def on_map(bundles, map_name, seed0=1):
    for i, B in enumerate(bundles):
        B.map_key, B.m, B.path = (map_name, "sha"), {"seed": seed0 + i}, f"{map_name}-{i}"
    return bundles


def roster(bundles):
    """Every slot a personality: the roster baseline."""
    for B in bundles:
        B.controller = {s: "personality" for s in B.slots}
    return bundles


def test_roster_separation_is_per_map_and_gates_only_on_every_map(fake_metric):
    # VEX passes (5 < 10) in all ten; the other six fake slots are the roster
    map10 = roster(on_map(fake_bundles([match(5, [20] * 5, 20)] * 4 + [match(5, [5, 20, 20, 20, 20], 20)]), "MAP10"))
    e1m5 = roster(on_map(fake_bundles([match(5, [5, 5, 20, 20, 20], 20)] * 5), "E1M5"))
    [x] = S.separation_vs_roster(map10 + e1m5, sheets(), ["VEX"])
    m = x["maps"]["MAP10"]["roster"]
    assert (m["passes"], m["computed"]) == (1, 30)  # VEX's own slot is not in the roster
    assert x["maps"]["MAP10"]["separates"] and not x["maps"]["E1M5"]["separates"]  # 97% and 67% fail
    assert x["separates_on"] == ["MAP10"] and not x["gates"]
    assert (x["passes"], x["matches"]) == (10, 10)


def test_roster_gate_needs_the_bot_to_pass_in_80_percent(fake_metric):
    eight = roster(on_map(fake_bundles([match(5, [20] * 5, 20)] * 8 + [match(50, [20] * 5, 20)] * 2), "MAP10"))
    [x] = S.separation_vs_roster(eight, sheets(), ["VEX"])
    assert x["gates"]
    seven = roster(on_map(fake_bundles([match(5, [20] * 5, 20)] * 7 + [match(50, [20] * 5, 20)] * 3), "MAP10"))
    [x] = S.separation_vs_roster(seven, sheets(), ["VEX"])
    assert x["separates_on"] == ["MAP10"] and not x["gates"]


def test_roster_leaves_out_na_and_placeholders(fake_metric):
    bundles = on_map(fake_bundles([match(5, [5, None, None, None, None], 20)]), "MAP10")  # 2, 4, 5, 7, 8 placeholders
    [x] = S.separation_vs_roster(bundles, sheets(), ["VEX"])
    assert (x["maps"]["MAP10"]["roster"]["computed"], x["maps"]["MAP10"]["roster"]["na"]) == (1, 0)  # only HEX


def test_behavior_distances_need_point_three_in_80_percent():
    far = {"A": {"behavior_shares": {"ROAMING": 1.0}}, "B": {"behavior_shares": {"ROAMING": 0.6, "HUNTING": 0.4}}}
    near = {"A": {"behavior_shares": {"ROAMING": 1.0}}, "B": {"behavior_shares": {"ROAMING": 0.8, "HUNTING": 0.2}}}
    assert S.total_variation({"ROAMING": 1.0}, {"ROAMING": 0.6, "HUNTING": 0.4}) == pytest.approx(0.4)
    eight = S.behavior_distances([{"bots": far}] * 8 + [{"bots": near}] * 2)["A/B"]
    assert (eight["far"], eight["ok"]) == (8, True)
    seven = S.behavior_distances([{"bots": far}] * 7 + [{"bots": near}] * 3)["A/B"]
    assert not seven["ok"]


def test_median_kill_distance_leaves_out_melee_kills(tmp_path):
    rows = [(0.0, 0.0, True)] * 20
    far = [(300.0, 0.0, True)] * 20
    B = write_bundle(tmp_path, {3: rows, 2: far}, [dict(kill(5), key="OB_MPCHAINSAW"), dict(kill(10), key="OB_MPROCKET")])
    assert S.median_kill_distance(B, 3, {}) == 300.0
    B2 = write_bundle(tmp_path, {3: rows, 2: far}, [dict(kill(5), key="OB_MPFIST")])
    assert S.median_kill_distance(B2, 3, {}) is None


def test_conjunction_passes_only_when_both_parts_pass(fake_metric, monkeypatch):
    monkeypatch.setitem(S.METRICS, "fake2", lambda B, s, sheet: B.values2.get(s))
    item = {"metric": "fake", "op": "<", "value": 10, "and": {"metric": "fake2", "op": ">", "value": 0.5}}
    assert S.label(item) == "fake < 10 AND fake2 > 0.5"
    assert S.item_passes(item, (5, 0.6)) and not S.item_passes(item, (5, 0.4)) and not S.item_passes(item, (5, None))
    bundles = roster(on_map(fake_bundles([match(5, [5, 5, 5, 20, 20], 5)] * 5), "MAP10"))
    for B in bundles:
        B.values2 = {1: 0.9, 2: 0.9, 4: 0.1, 5: 0.1, 6: 0.1, 7: 0.9, 8: 0.9}
    shs = [{"code": c, "signature": [dict(item, gate=True)] if c == "VEX" else []} for c in ("VEX", "KRN", "ASH", "ORB", "HEX", "PYR", "ZED")]
    [x] = S.separation_vs_roster(bundles, shs, ["VEX"])
    r = x["maps"]["MAP10"]["roster"]
    assert (r["passes"], r["computed"]) == (5, 30)  # only KRN (slot 2) passes both: fake 5 < 10 and 0.9 > 0.5
    assert x["gates"] and x["accepts"]
