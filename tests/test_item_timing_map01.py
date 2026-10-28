"""Item timing end to end (milestone 2D): KRN, alone on MAP01, is on a GreenArmor within 2 s of its respawn.

ViZDoom runs a one-player deathmatch with the match's host arguments plus cheats. The script makes the bot
pick up the GreenArmor (the bot's own pickup starts its timer), then sends it to a far deathmatch start with
90 armor and 15 health, and puts both back whenever it picks up anything elsewhere. The GreenArmor can still
be taken (armor under 100), but the bot is badly hurt and MAP01's standing health items pull it the other
way: COLLECTING_HEALTH, as when KRN arrived 650 to 5,000 tics late in the 2C evaluation. The respawn is
1050 tics after the pickup (make bench-items); the bot must take the item again in [respawn, respawn + 70].
Before the milestone 2D fix it never went back.

Needs ViZDoom (run it in the image: make docker-test); skipped without it. About a minute on the homelab box.
"""

import math
import os

import pytest

vzd = pytest.importorskip("vizdoom")

from engine import game, items, peers, roster  # noqa: E402
from engine.personality.controller import PersonalityBot  # noqa: E402
from tools import extract_map  # noqa: E402

ARMOR = (1088.0, 368.0)  # MAP01's GreenArmor (Freedoom 0.13)
FAR = (-144.0, -1024.0)  # a deathmatch start across the map
SLACK = 70
RESET = ["take armor", "give ArmorBonus 90"]  # 90 armor: the GreenArmor (cap 100) can still be taken
HURT = 15
CACHE = os.path.join(roster.ROOT, "out", "test-cache")


def near(v, p, r=64.0):
    return math.hypot(v["POSITION_X"] - p[0], v["POSITION_Y"] - p[1]) <= r


def test_krn_is_on_a_known_respawn_within_2_s():
    iwad = extract_map.default_iwad("MAP01")
    md = extract_map.extract(iwad, "MAP01")
    assert any(i["name"] == "GreenArmor" and (i["x"], i["y"]) == ARMOR for i in md["items"])
    sheet = next(sh for sh in roster.load() if sh["code"] == "KRN")
    assert sheet["items"]["item_timing"]
    g = game.make_game(iwad=iwad, map_name="MAP01", seed=5, wads=[],
                       args=game.host_args(1, 5161, 2, cheats=True))
    g.init()
    bot = PersonalityBot(slot=2, sheet=sheet, seed=5, map_data=md, cache_dir=CACHE)
    bot.identity = peers.NoIdentity()
    picked = again = None
    prev_armor = prev_health = 0.0
    try:
        for t in range(3000):
            st = g.get_state()
            v = dict(zip(game.VARIABLES, st.game_variables))
            action, cmds, _, _, _ = bot.act(t, st, v)
            rose = v["ARMOR"] > prev_armor
            healed = v["HEALTH"] > prev_health
            prev_armor, prev_health = v["ARMOR"], v["HEALTH"]
            if picked is None:
                if t == 5:
                    cmds += ["take armor", "warp %d %d" % ARMOR]
                if 8 <= t < 12:  # a warp touches nothing: step across the item
                    action = {"MOVE_FORWARD": 1} if t < 10 else {"MOVE_BACKWARD": 1}
                if rose and near(v, ARMOR):
                    picked = t
            elif t == picked + 5:
                cmds += ["warp %d %d" % FAR] + RESET + ["take health %d" % (v["HEALTH"] - HURT)]
            elif t > picked + 5:
                if rose and near(v, ARMOR):
                    again = t
                    break
                if rose:
                    cmds += RESET  # armor from anything else: back to 90
                if healed and v["HEALTH"] > HURT:
                    cmds += ["take health %d" % (v["HEALTH"] - HURT)]  # still hurt: health items keep their pull
            if t == 40:
                assert picked is not None, "the scripted pickup did not happen"
            for c in cmds:
                g.send_game_command(c)
            g.make_action(game.action_vector(action), 1)
            if picked is not None and t > picked + items.RESPAWN_TICS + 4 * SLACK:
                break
    finally:
        g.close()
    respawn = picked + items.RESPAWN_TICS
    assert again is not None, f"never took the GreenArmor again (picked at {picked}, respawn {respawn})"
    assert respawn <= again <= respawn + SLACK, f"took it at {again}, respawn {respawn}"
