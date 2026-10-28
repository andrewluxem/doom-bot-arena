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
