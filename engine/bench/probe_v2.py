"""Event-capture feasibility probe v2 (scratch, not repo code).

8-player ViZDoom deathmatch, -nomonsters, synchronous, one make_action(.., 1) per
loop, optional per-step barrier across processes, cheats on so the script can
stage deaths. Every process logs per tic its own variables and the raw
notifications buffer. Scenarios:
  1. weapon kill   P2 kills P1 with a super shotgun
  2. telefrag      P2 parks on teleport destination, P3 walks into the teleporter
  3. self-kill     P1 fires rockets into a wall
  4. barrel kill   P3 shoots a barrel next to P2
"""

import argparse
import json
import math
import multiprocessing as mp
import os
import struct
import sys
import time

import vizdoom as vzd

sys.path.insert(0, "/repo/tools")
import extract_map  # noqa: E402

NAMES = ["Vex", "Kronos", "Null", "Ash", "Orbit", "Hex", "Pyre", "Zed"]
PORT = 5029
VARS = [vzd.GameVariable.PLAYER_NUMBER, vzd.GameVariable.FRAGCOUNT, vzd.GameVariable.DEATHCOUNT,
        vzd.GameVariable.DEAD, vzd.GameVariable.HEALTH, vzd.GameVariable.POSITION_X,
        vzd.GameVariable.POSITION_Y, vzd.GameVariable.ANGLE] + \
       [getattr(vzd.GameVariable, f"PLAYER{i}_FRAGCOUNT") for i in range(1, 9)]
BUTTONS = [vzd.Button.ATTACK, vzd.Button.MOVE_FORWARD, vzd.Button.TURN_LEFT_RIGHT_DELTA]

# Every obituary key ClientObituary can emit for a player victim in Doom-format DM.
OBIT_KEYS = ["OB_SUICIDE", "OB_FALLING", "OB_CRUSH", "OB_EXIT", "OB_WATER", "OB_SLIME", "OB_LAVA",
             "OB_VOODOO", "OB_KILLEDSELF", "OB_MONTELEFRAG", "OB_FRIENDLY1", "OB_FRIENDLY2",
             "OB_FRIENDLY3", "OB_FRIENDLY4", "OB_MPTELEFRAG", "OB_DEFAULT", "OB_BARREL", "OB_MPFIST",
             "OB_MPCHAINSAW", "OB_MPPISTOL", "OB_MPSHOTGUN", "OB_MPSSHOTGUN", "OB_MPCHAINGUN",
             "OB_MPROCKET", "OB_MPR_SPLASH", "OB_MPPLASMARIFLE", "OB_MPBFG_BOOM", "OB_MPBFG_SPLASH",
             "OB_MPBFG_MBF", "OB_RAILGUN", "OB_GRENADE"]


def make_obit_wad(path):
    text = "Patch File for DeHackEd v3.0\nDoom version = 21\nPatch format = 6\n\n[STRINGS]\n"
    text += "".join(f"{k} = OBIT|{k}|%o|%k\n" for k in OBIT_KEYS)
    body = text.encode("ascii")
    with open(path, "wb") as f:
        f.write(struct.pack("<4sii", b"PWAD", 1, 12 + len(body)))
        f.write(body)
        f.write(struct.pack("<ii8s", 12, len(body), b"DEHACKED"))


# ---------------------------------------------------------------- geometry
def seg_cross(p, q, a, b):
    def orient(o, s, t):
        return (s[0] - o[0]) * (t[1] - o[1]) - (s[1] - o[1]) * (t[0] - o[0])
    d1, d2 = orient(a, b, p), orient(a, b, q)
    d3, d4 = orient(p, q, a), orient(p, q, b)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def dist_ps(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


class Geo:
    def __init__(self, m):
        self.m = m
        self.lines = [((l["x1"], l["y1"]), (l["x2"], l["y2"]), l) for l in m["linedefs"]]

    def clear(self, p, q, skip=None):
        return not any(seg_cross(p, q, a, b) for a, b, l in self.lines if l["id"] != skip)

    def clearance(self, p, skip=None):
        return min(dist_ps(p, a, b) for a, b, l in self.lines if l["id"] != skip)

    def plan(self):
        m = self.m
        plan = {}
        anchors = [(w["x"], w["y"]) for w in m["weapons"] if w["in_deathmatch"]]
        for d in anchors:
            for k in range(24):
                th = k * math.pi / 12
                a = (round(d[0] + 48 * math.cos(th)), round(d[1] + 48 * math.sin(th)))
                b = (round(d[0] - 48 * math.cos(th)), round(d[1] - 48 * math.sin(th)))
                if self.clear(a, b) and self.clearance(a) >= 32 and self.clearance(b) >= 32:
                    plan["kill"] = {"A": a, "B": b}
                    break
            if "kill" in plan:
                break
        # MAP01 tag-5 teleporter: lines 937/938/940 around (544,-608) -> destination (1312,1120)
        plan["telefrag"] = {"dest": (1312, 1120), "approach": (598, -608), "face_deg": 180.0}
        for a, b, l in self.lines:
            if l["two_sided"]:
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L < 128:
                continue
            nx, ny = dy / L, -dx / L
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            w = (round(mid[0] + 40 * nx), round(mid[1] + 40 * ny))
            if self.clearance(w, skip=l["id"]) >= 40 and self.clear(w, mid, skip=l["id"]):
                plan["selfkill"] = {"W": w, "face_deg": math.degrees(math.atan2(-ny, -nx)) % 360, "linedef": l["id"]}
                break
        barrels = [(b["x"], b["y"]) for b in m["barrels"] if b["in_deathmatch"]]
        for bar in barrels:
            for i in range(16):
                tu = i * math.pi / 8
                v = (round(bar[0] + 36 * math.cos(tu)), round(bar[1] + 36 * math.sin(tu)))
                if self.clearance(v) < 20 or not self.clear(v, bar):
                    continue
                for j, r in ((j, r) for r in (112, 96, 144, 176) for j in range(4, 13)):
                    tw = tu + j * math.pi / 8
                    s = (round(bar[0] + r * math.cos(tw)), round(bar[1] + r * math.sin(tw)))
                    if (self.clearance(s) >= 32 and self.clear(s, bar) and dist_ps(v, s, bar) >= 34
                            and min(math.dist(s, o) for o in barrels) >= 100):
                        plan["barrel"] = {"barrel": bar, "V": v, "S": s}
                        break
                if "barrel" in plan:
                    break
            if "barrel" in plan:
                break
        return plan


def angle_to(src, dst):
    return math.degrees(math.atan2(dst[1] - src[1], dst[0] - src[0])) % 360


def wrap180(a):
    return (a + 180) % 360 - 180


class Script:
    def __init__(self, slot, plan):
        self.slot, self.plan = slot, plan
        self.face = None
        self.fire = None  # (every, lo, hi)
        self.walk = None  # (lo, hi)
        self.last_fire = -999

    def step(self, t, v):
        cmds = []
        p, s = self.plan, self.slot
        if t == 140:
            if s == 1:
                cmds.append("warp %d %d" % tuple(p["kill"]["A"]))
            if s == 2:
                cmds += ["warp %d %d" % tuple(p["kill"]["B"]), "give supershotgun", "give ammo", "use SuperShotgun"]
        if s == 2 and t == 150:
            self.face, self.fire = angle_to(p["kill"]["B"], p["kill"]["A"]), (40, 175, 400)
        if t == 500 and s == 2:
            self.face, self.fire = None, None
            cmds.append("warp %d %d" % tuple(p["telefrag"]["dest"]))
        if t == 540 and s == 3:
            cmds.append("warp %d %d" % tuple(p["telefrag"]["approach"]))
        if t == 545 and s == 3:
            self.face, self.walk = p["telefrag"]["face_deg"], (560, 620)
        if t == 640 and s == 1:
            cmds += ["warp %d %d" % tuple(p["selfkill"]["W"]), "give rocketlauncher", "give ammo", "use RocketLauncher"]
        if t == 650 and s == 1:
            self.face, self.fire = p["selfkill"]["face_deg"], (30, 690, 900)
        if t == 960:
            if s == 2:
                cmds.append("warp %d %d" % tuple(p["barrel"]["V"]))
            if s == 3:
                self.face, self.walk = None, None
                cmds += ["warp %d %d" % tuple(p["barrel"]["S"]), "use Pistol"]
        if t == 970 and s == 3:
            self.face, self.fire = angle_to(p["barrel"]["S"], p["barrel"]["barrel"]), (18, 1000, 1300)

        attack = fwd = 0
        turn = 0.0
        alive = not v["DEAD"]
        if self.face is not None and alive:
            turn = -wrap180(self.face - v["ANGLE"])
        if self.fire and alive:
            every, lo, hi = self.fire
            if lo <= t <= hi and t - self.last_fire >= every:
                attack, self.last_fire = 1, t
        if self.walk and alive and self.walk[0] <= t <= self.walk[1]:
            fwd = 1
        return [attack, fwd, turn], cmds


def player(slot, n, tics, plan, outdir, wads, seed, delay, barrier):
    g = vzd.DoomGame()
    g.set_doom_game_path(extract_map.default_iwad())
    g.set_doom_map("MAP01")
    g.set_mode(vzd.Mode.PLAYER)
    g.set_screen_format(vzd.ScreenFormat.RGB24)
    g.set_screen_resolution(vzd.ScreenResolution.RES_320X240)
    g.set_window_visible(False)
    g.set_sound_enabled(False)
    g.set_notifications_buffer_enabled(True)
    g.set_notifications_buffer_size(1)
    g.set_available_buttons(BUTTONS)
    g.set_available_game_variables(VARS)
    g.set_seed(seed)
    if slot == 1:
        g.add_game_args(f"-host {n} -port {PORT} -netmode 0 -deathmatch -nomonsters +sv_forcerespawn 1 "
                        f"+sv_noautoaim 1 +sv_respawnprotect 1 +sv_spawnfarthest 1 +sv_nocrouch 1 "
                        f"+sv_noexit 1 +viz_nocheat 0 +viz_respawn_delay {delay} +sv_cheats 1")
    else:
        g.add_game_args(f"-join 127.0.0.1 -port {PORT}")
    g.add_game_args(f"+name {NAMES[slot - 1]} +colorset {slot - 1} +cl_bbannounce 0 +show_obituaries 1")
    for w in wads:
        g.add_game_args(f"-file {w}")
    g.init()
    script = Script(slot, plan)
    names = [v.name for v in VARS]
    t0 = time.time()
    with open(os.path.join(outdir, f"p{slot}.jsonl"), "w") as f:
        for _ in range(tics):
            if g.is_episode_finished():
                break
            st = g.get_state()
            vals = dict(zip(names, st.game_variables))
            et = g.get_episode_time()
            action, cmds = script.step(et, vals)
            for c in cmds:
                g.send_game_command(c)
            f.write(json.dumps({"episode_time": et, "tic": st.tic, "number": st.number,
                                **{k: float(x) for k, x in vals.items()},
                                "notif": st.notifications_buffer, "cmds": cmds, "action": action}) + "\n")
            g.make_action(action, 1)
            if barrier is not None:
                barrier.wait(60)
    print(f"slot {slot} done in {time.time() - t0:.1f}s", flush=True)
    if barrier is not None:
        try:
            barrier.wait(60)
        except Exception:
            pass
    g.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tics", type=int, default=1400)
    ap.add_argument("--players", type=int, default=8)
    ap.add_argument("--obit-override", action="store_true")
    ap.add_argument("--no-barrier", action="store_true")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--respawn-delay", type=int, default=2)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    wads = []
    if a.obit_override:
        wads.append(os.path.join(a.out, "obits.wad"))
        make_obit_wad(wads[0])
    m = extract_map.extract(extract_map.default_iwad(), "MAP01")
    plan = Geo(m).plan()
    json.dump(plan, open(os.path.join(a.out, "plan.json"), "w"))
    print("plan", json.dumps(plan), flush=True)
    barrier = None if a.no_barrier else mp.Barrier(a.players)
    procs = []
    for slot in range(1, a.players + 1):
        pr = mp.Process(target=player, args=(slot, a.players, a.tics, plan, a.out, wads, a.seed,
                                              a.respawn_delay, barrier))
        pr.start()
        procs.append(pr)
        if slot == 1:
            time.sleep(1.0)
    for pr in procs:
        pr.join(600)
        print("exit", pr.exitcode, flush=True)


if __name__ == "__main__":
    main()
