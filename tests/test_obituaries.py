"""Obituary parser tests (docs/MATCH_FORMAT.md section 6.1).

The expected outcome of every key is written out here instead of read from
obituaries.KEYS, so changing the table means changing this file too.
"""

import importlib.util
import os
import re
import struct
import zipfile

import pytest

from engine import bundle, obituaries
from tools import extract_map

NAMES = {"Vex": 1, "Kronos": 2, "Null": 3, "Ash": 4, "Orbit": 5, "Hex": 6, "Pyre": 7, "Zed": 8}

# key -> (event type when killer == victim, event type when killer != victim); None = rejected
WEAPON = (None, "frag")
EXPECTED = {
    "OB_SUICIDE": ("suicide", None),
    "OB_FALLING": ("environmental_death", None),
    "OB_CRUSH": ("environmental_death", None),
    "OB_EXIT": ("suicide", None),
    "OB_WATER": ("environmental_death", None),
    "OB_SLIME": ("environmental_death", None),
    "OB_LAVA": ("environmental_death", None),
    "OB_MONTELEFRAG": ("environmental_death", None),
    "OB_VOODOO": (None, None),
    "OB_FRIENDLY1": (None, None),
    "OB_FRIENDLY2": (None, None),
    "OB_FRIENDLY3": (None, None),
    "OB_FRIENDLY4": (None, None),
    "OB_KILLEDSELF": ("suicide", None),
    "OB_MPTELEFRAG": (None, "telefrag"),
    "OB_DEFAULT": ("environmental_death", "frag"),
    "OB_BARREL": WEAPON,
    "OB_MPFIST": WEAPON,
    "OB_MPCHAINSAW": WEAPON,
    "OB_MPPISTOL": WEAPON,
    "OB_MPSHOTGUN": WEAPON,
    "OB_MPSSHOTGUN": WEAPON,
    "OB_MPCHAINGUN": WEAPON,
    "OB_MPROCKET": WEAPON,
    "OB_MPR_SPLASH": WEAPON,
    "OB_MPPLASMARIFLE": WEAPON,
    "OB_MPBFG_BOOM": WEAPON,
    "OB_MPBFG_SPLASH": WEAPON,
    "OB_MPBFG_MBF": WEAPON,
    "OB_RAILGUN": WEAPON,
    "OB_GRENADE": WEAPON,
}
CASES = [(key, same) for key in EXPECTED for same in (True, False)]


def case_id(case):
    key, same = case
    return f"{key}-{'self' if same else 'other'}"


def obit(key, victim, killer):
    return f"OBIT|{key}|{victim}|{killer}"


def assert_rejected(line, reason):
    ob, warn = obituaries.parse(line, set(NAMES))
    assert ob is None
    assert reason in warn
    assert bundle.death_events([(50, line)], NAMES) == ([], [f"tic 50: {warn}"])


# ------------------------------------------------------------------ the 31 keys
def test_table_has_the_31_keys():
    assert len(EXPECTED) == 31
    assert set(obituaries.KEYS) == set(EXPECTED)


def test_wad_overrides_each_key_once():
    data = obituaries.wad_bytes()
    ident, count, offset = struct.unpack_from("<4sii", data, 0)
    assert (ident, count) == (b"PWAD", 1)
    pos, size, name = struct.unpack_from("<ii8s", data, offset)
    assert name == b"DEHACKED"
    lines = data[pos:pos + size].decode("ascii").splitlines()
    strings = lines[lines.index("[STRINGS]") + 1:]
    assert sorted(strings) == sorted(f"{key} = OBIT|{key}|%o|%k" for key in EXPECTED)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_key_parses(case):
    key, same = case
    killer = "Vex" if same else "Kronos"
    line = obit(key, "Vex", killer)
    assert obituaries.parse(line, set(NAMES)) == (obituaries.Obituary(key, "Vex", killer, line), None)


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_key_classifies(case):
    key, same = case
    killer = "Vex" if same else "Kronos"
    line = obit(key, "Vex", killer)
    death, warn = obituaries.classify(obituaries.parse(line, set(NAMES))[0])
    expected = EXPECTED[key][0 if same else 1]
    if expected is None:
        assert death is None
        assert "not attributable" in warn
        return
    actor = None if expected == "environmental_death" else killer
    assert (death, warn) == (obituaries.Death(expected, actor, "Vex", key, line), None)


@pytest.mark.parametrize("tic", [0, 50])
@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_key_becomes_one_event_or_none(case, tic):
    key, same = case
    killer = "Vex" if same else "Kronos"
    line = obit(key, "Vex", killer)
    events, warnings = bundle.death_events([(tic, line)], NAMES)
    expected = EXPECTED[key][0 if same else 1]
    if expected is None:
        assert events == []
        assert len(warnings) == 1
        return
    env = expected == "environmental_death"
    assert warnings == []
    assert events == [{"tic": tic, "type": expected, "actor": None if env else NAMES[killer], "target": 1,
                       "key": key, "credited": None if env else tic > 0, "source": line}]


# ------------------------------------------------------------------ OB_EXIT and OB_SUICIDE
@pytest.mark.parametrize("key", ["OB_EXIT", "OB_SUICIDE"])
def test_exit_and_suicide_are_self_kills(key):
    line = obit(key, "Kronos", "Kronos")
    events, warnings = bundle.death_events([(900, line)], NAMES)
    assert warnings == []
    assert events == [{"tic": 900, "type": "suicide", "actor": 2, "target": 2, "key": key,
                       "credited": True, "source": line}]


@pytest.mark.parametrize("key", ["OB_EXIT", "OB_SUICIDE"])
def test_exit_and_suicide_with_another_killer_are_rejected(key):
    death, warn = obituaries.classify(obituaries.parse(obit(key, "Kronos", "Vex"), set(NAMES))[0])
    assert death is None
    assert "not attributable" in warn


@pytest.mark.parametrize("key", ["OB_EXIT", "OB_SUICIDE"])
def test_exit_and_suicide_cost_one_frag(key):
    events, _ = bundle.death_events([(900, obit(key, "Kronos", "Kronos"))], NAMES)
    rows = [{"tic": t, "slot": 2, "alive": t < 900} for t in range(899, 902)]
    assert bundle.reconcile(events, rows, [{"slot": 2, "fragcount": -1, "deathcount": 1}]) == []
    assert bundle.reconcile(events, rows, [{"slot": 2, "fragcount": 0, "deathcount": 1}]) == [
        "slot 2: engine FRAGCOUNT 0 != events -1"]


# ------------------------------------------------------------------ malformed lines
@pytest.mark.parametrize("line, reason", [
    ("OBIT|OB_MPSHOTGUN|Vex", "malformed"),
    ("OBIT|OB_MPSHOTGUN|Vex|Kronos|", "malformed"),
    ("OBIT|OB_MPSHOTGUN|Vex|Kronos|Null", "malformed"),
    ("OBIT|", "malformed"),
    ("OBIT|||", "unknown obituary key"),
    ("OBIT|OB_MPSHOTGUNS|Vex|Kronos", "unknown obituary key"),
    ("OBIT|ob_mpshotgun|Vex|Kronos", "unknown obituary key"),
    ("OBIT|OB_STEALTHIMP|Vex|Kronos", "unknown obituary key"),  # a real ZDoom key, not a player obituary
    ("OBIT|OB_MPSHOTGUN|Vex|Doomguy", "not in this match"),
    ("OBIT|OB_MPSHOTGUN||Kronos", "not in this match"),
    ("OBIT|OB_MPSHOTGUN|vex|Kronos", "not in this match"),
    ("OBIT|OB_MPSHOTGUN|Vex|Kronos ", "not in this match"),
    ("OBIT|OB_MPSHOTGUN|Vex|%k", "not in this match"),
    (" OBIT|OB_MPSHOTGUN|Vex|Kronos", "unknown notification"),
    ("obit|OB_MPSHOTGUN|Vex|Kronos", "unknown notification"),
    ("Vex: OBIT|OB_MPSHOTGUN|Kronos|Vex", "unknown notification"),  # chat
    ("", "unknown notification"),
])
def test_malformed_lines_are_rejected(line, reason):
    assert_rejected(line, reason)


@pytest.mark.parametrize("name", ["Ve|x", "", "Sixteen_chars_xx", "Vex Two", "%k"])
def test_names_that_break_the_format_are_refused(name):
    with pytest.raises(ValueError):
        obituaries.check_names(["Vex", name])


def test_duplicate_names_are_refused():
    with pytest.raises(ValueError):
        obituaries.check_names(["Vex", "Vex"])


# ------------------------------------------------------------------ stock obituary text
# Printed by ViZDoom 1.3.2 with Freedoom 0.13 when obituaries.wad is not loaded (section 6.2).
OBSERVED_STOCK = [
    "Vex was permeated by Kronos's double-barreled shotgun.",
    "Kronos stood where Null was teleporting.",
    "Vex stood where Kronos was teleporting.",
    "Vex killed himself.",
    "Kronos went boom.",
]
PRONOUNS = {"g": "he", "h": "him", "p": "his", "s": "his", "r": "he's"}


def installed_stock_text():
    """Each key's stock English obituary as this ViZDoom install prints it: the engine's
    language.enu, overridden by the IWAD's DEHACKED, with Vex as victim and Kronos as killer."""
    spec = importlib.util.find_spec("vizdoom")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("ViZDoom not installed")
    base = spec.submodule_search_locations[0]
    templates = {}
    with zipfile.ZipFile(os.path.join(base, "vizdoom.pk3")) as pk3:
        for m in re.finditer(r'^\s*(OB_\w+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', pk3.read("language.enu").decode(), re.M):
            templates[m[1]] = m[2].replace('\\"', '"')
    with open(os.path.join(base, "freedoom2.wad"), "rb") as f:
        data = f.read()
    for name, pos, size in extract_map.read_directory(data)[1]:
        if name == "DEHACKED":
            for m in re.finditer(r"^\s*(OB_\w+)\s*=\s*(.*?)\s*$", data[pos:pos + size].decode("latin-1"), re.M):
                templates[m[1]] = m[2]
    fill = {"o": "Vex", "k": "Kronos", **PRONOUNS}
    return {key: re.sub(r"%([a-z])", lambda m: fill.get(m[1], m[0]), templates[key])
            for key in EXPECTED if key in templates}


@pytest.mark.parametrize("line", OBSERVED_STOCK)
def test_observed_stock_text_is_rejected(line):
    assert_rejected(line, "unknown notification")


def test_installed_stock_text_is_rejected():
    stock = installed_stock_text()
    assert len(stock) >= 30  # OB_GRENADE has no English string in the pinned engines
    for key, line in stock.items():
        assert "OBIT" not in line, key
        assert_rejected(line, "unknown notification")


def test_stock_text_never_reaches_the_obituary_list():
    notes = [{"tic": 10, "line": line} for line in OBSERVED_STOCK]
    notes.append({"tic": 12, "line": obit("OB_BARREL", "Kronos", "Null")})
    lines, unknown, problems = bundle.agreed_obituary_lines({1: notes, 2: notes})
    assert lines == [(12, "OBIT|OB_BARREL|Kronos|Null")]
    assert unknown == [(s, 10, line) for s in (1, 2) for line in OBSERVED_STOCK]
    assert problems == []
