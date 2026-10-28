"""Pocket spawns (milestone 2E): a bot spawned outside the largest nav region takes the pocket's exit within 30 s.

In the 2D holdout (E1M5, seed 501) KRN spawned at (-1432, 720), in E1M5's exit room, and never moved: its exit
goal snapped to a main-region node through the wall, so no path existed and it stood still all match. Here KRN
and VEX each play a one-player deathmatch on E1M5 with the match's host arguments plus cheats, are warped to
that spot, and must die on the exit switch (OB_EXIT under sv_noexit) within 30 s of the warp, at the switch.

Needs ViZDoom (run it in the image: make docker-test); skipped without it.
"""

import math
import os

import pytest

vzd = pytest.importorskip("vizdoom")

from engine import game, peers, roster  # noqa: E402
from engine.personality.controller import PersonalityBot  # noqa: E402
from tools import extract_map  # noqa: E402

POCKET = (-1432.0, 720.0)
WARP_TIC = 3
LIMIT = 30 * 35
CACHE = os.path.join(roster.ROOT, "out", "test-cache")


@pytest.mark.parametrize("code", ["KRN", "VEX"])
def test_pocket_spawn_takes_the_exit_within_30_s(code):
    iwad = extract_map.default_iwad("E1M5")
    md = extract_map.extract(iwad, "E1M5")
    sheet = next(sh for sh in roster.load() if sh["code"] == code)
    g = game.make_game(iwad=iwad, map_name="E1M5", seed=5, wads=[], args=game.host_args(1, 5164, 2, cheats=True))
    g.init()
    bot = PersonalityBot(slot=sheet["slot"], sheet=sheet, seed=5, map_data=md, cache_dir=CACHE)
    bot.identity = peers.NoIdentity()
    n = bot.graph.nearest(*POCKET)
    assert bot.graph.region_id[n] != 0, "the test spot must be outside the largest region"
    died = where = None
    try:
        for t in range(WARP_TIC + LIMIT + 1):
            st = g.get_state()
            v = dict(zip(game.VARIABLES, st.game_variables))
            if v["DEAD"]:
                died = t
                break
            where = (v["POSITION_X"], v["POSITION_Y"])
            action, cmds, _, _, _ = bot.act(t, st, v)
            if t == WARP_TIC:
                cmds = cmds + ["warp %d %d" % POCKET]
            for c in cmds:
                g.send_game_command(c)
            g.make_action(game.action_vector(action), 1)
    finally:
        g.close()
    assert died is not None, f"{code} still alive {LIMIT} tics after the warp, at {where}"
    assert died - WARP_TIC <= LIMIT
    _, approach, mid = bot.exits[0]
    assert math.hypot(where[0] - mid[0], where[1] - mid[1]) < 96, f"died at {where}, not at the exit switch {mid}"
