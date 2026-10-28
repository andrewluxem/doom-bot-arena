"""Controlled bench: staged deaths with known answers, checked against the captured source.

    python -m engine.bench.controlled --seed 1234 --out out/bench

Eight players, cheats on so the script can `warp` and `give`. The stages are
the ones that settled the event-capture gate:
  1. weapon kill   slot 2 kills slot 1 with a super shotgun
  2. telefrag      slot 2 stands on the MAP01 teleport destination, slot 3 walks into the teleporter
  3. self-kill     slot 1 fires rockets into a wall
  4. barrel kill   slot 3 shoots a barrel next to slot 2
Slots 7 and 8 are static observers, each facing a wall in a part of the map
with no light effects. Slot 7 renders like a capture client (messages off);
slot 8 renders messages as a positive control. Their frames show whether
message text reaches the top of the POV. (Two players cannot share one spot:
warping both onto the same point in the same tic telefragged one of them.)

Needs MAP01 from the Freedoom 0.13 IWAD shipped with ViZDoom 1.3.2: the
teleporter coordinates below are specific to it.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys

import numpy as np

from engine import bundle, match, roster
from engine.runner import build, preflight, sha256_file
from tools import extract_map

FREEDOOM_013_SHA256 = "a8772e088847032510d97ba2312406a6998f21cbab44d4ff10696faa9c0ecd4b"
# Tag-5 teleporter lines 937/938/940 around (544,-608) send the player to the destination thing
# at (1312,1120). Sector 86 east of line 938 is only 40 units wide; (598,-608) clears the door at x=616.
TELEPORTER = {"dest": (1312, 1120), "approach": (598, -608), "face_deg": 180.0}
OBSERVERS = (7, 8)
STATIC_LIGHT_SPECIALS = {0, 9}  # 9 is "secret", not a light effect
BAND_ROWS = 64  # top of the 240-row frame, where ZDoom draws notify messages
DURATION = 1400
DUMP_EVERY = 5
RESPAWN_DELAY = 2


# ------------------------------------------------------------------ geometry
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
    def __init__(self, m, things=()):
        self.m = m
        self.things = list(things)
        self.lines = [((l["x1"], l["y1"]), (l["x2"], l["y2"]), l) for l in m["linedefs"]]

    def clear(self, p, q, skip=None):
        return not any(seg_cross(p, q, a, b) for a, b, l in self.lines if l["id"] != skip)

    def clearance(self, p, skip=None):
        return min(dist_ps(p, a, b) for a, b, l in self.lines if l["id"] != skip)

    def quiet_light(self, p, radius=256):
        """No light effects in any sector touching a line within radius of p."""
        secs = self.m["sectors"]
        for a, b, l in self.lines:
            if dist_ps(p, a, b) <= radius:
                for side in (l["front_sector"], l["back_sector"]):
                    if side is not None and secs[side]["special"] not in STATIC_LIGHT_SPECIALS:
                        return False
        return True

    def plan(self):
        m = self.m
        plan = {"telefrag": dict(TELEPORTER)}
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
        for a, b, l in self.lines:
            if l["two_sided"]:
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L < 128:
                continue
            nx, ny = dy / L, -dx / L  # front side is right of v1->v2
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            w = (round(mid[0] + 40 * nx), round(mid[1] + 40 * ny))
            if self.clearance(w, skip=l["id"]) >= 40 and self.clear(w, mid, skip=l["id"]):
                plan["selfkill"] = {"W": w, "face_deg": math.degrees(math.atan2(-ny, -nx)) % 360}
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
        # Observers: 48 units in front of a long solid wall, facing it, so the wall fills the frame.
        # Away from the stages, spawn spots and every item (a pickup prints a message), with no light
        # effects nearby.
        busy = [plan["kill"]["A"], plan["kill"]["B"], TELEPORTER["dest"], TELEPORTER["approach"],
                plan["selfkill"]["W"], plan["barrel"]["V"], plan["barrel"]["S"]] + barrels
        starts = [(s["x"], s["y"]) for s in m["deathmatch_starts"]]
        chosen = []
        for a, b, l in self.lines:
            if l["two_sided"] or len(chosen) == len(OBSERVERS):
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L < 192:
                continue
            nx, ny = dy / L, -dx / L
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            c = (round(mid[0] + 48 * nx), round(mid[1] + 48 * ny))
            if (self.clearance(c, skip=l["id"]) >= 40 and self.clear(c, mid, skip=l["id"])
                    and min(math.dist(c, q) for q in busy + [o["P"] for o in chosen]) >= 256
                    and min(math.dist(c, q) for q in starts) >= 96
                    and min(math.dist(c, t) for t in self.things) >= 64 and self.quiet_light(c)):
                chosen.append({"P": c, "face_deg": math.degrees(math.atan2(-ny, -nx)) % 360, "linedef": l["id"]})
        if len(chosen) == len(OBSERVERS):
            plan["observers"] = dict(zip(OBSERVERS, chosen))
        missing = {"kill", "selfkill", "barrel", "observers"} - set(plan)
        if missing:
            raise RuntimeError(f"no placement found for {sorted(missing)}")
        return plan


def map_things(iwad, map_name):
    """Every thing position in the map (items included), read straight from the WAD."""
    with open(iwad, "rb") as f:
        data = f.read()
    _, lumps = extract_map.read_directory(data)
    found = extract_map.map_lumps(data, lumps, map_name)
    return [(x, y) for x, y, _, _, _ in extract_map.unpack_all("<hhhhH", found["THINGS"])]


def angle_to(src, dst):
    return math.degrees(math.atan2(dst[1] - src[1], dst[0] - src[0])) % 360


def wrap180(a):
    return (a + 180) % 360 - 180


# ------------------------------------------------------------------ scripted players
class StagedController:
    def __init__(self, slot, sheet, seed, plan):
        self.slot, self.plan = slot, plan
        self.face = None
        self.fire = None  # (every, first, last)
        self.walk = None  # (first, last)
        self.last_fire = -999

    def act(self, t, state, v):
        cmds, p, s = [], self.plan, self.slot
        if t == 100 and s in OBSERVERS:
            obs = p["observers"][s] if s in p["observers"] else p["observers"][str(s)]
            cmds.append("warp %d %d" % tuple(obs["P"]))
            self.face = obs["face_deg"]
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

        action, behavior = {}, "HOLDING_ANGLE"
        if self.face is not None and abs(wrap180(self.face - v["ANGLE"])) > 1e-6:
            action["TURN_LEFT_RIGHT_DELTA"] = -wrap180(self.face - v["ANGLE"])
        if self.fire:
            every, lo, hi = self.fire
            if lo <= t <= hi:
                behavior = "IN_COMBAT"
                if t - self.last_fire >= every:
                    action["ATTACK"], self.last_fire = 1, t
        if self.walk and self.walk[0] <= t <= self.walk[1]:
            action["MOVE_FORWARD"], behavior = 1, "ROAMING"
        return action, cmds, behavior, None


EXPECTED = [  # (stage, type, actor, target, key, first tic, last tic)
    ("weapon kill", "frag", 2, 1, "OB_MPSSHOTGUN", 175, 420),
    ("teleporter telefrag", "telefrag", 3, 2, "OB_MPTELEFRAG", 560, 640),
    ("self-kill", "suicide", 1, 1, "OB_KILLEDSELF", 690, 940),
    ("barrel kill", "frag", 3, 2, "OB_BARREL", 1000, 1320),
]


# ------------------------------------------------------------------ checks
def check_stages(events):
    problems, found = [], []
    later = [e for e in events if e["type"] != "respawn" and e["tic"] > 0]
    used = set()
    for stage, typ, actor, target, key, lo, hi in EXPECTED:
        hits = [i for i, e in enumerate(later) if e["type"] == typ and e["actor"] == actor
                and e["target"] == target and e["key"] == key and lo <= e["tic"] <= hi and e["credited"]]
        if len(hits) != 1:
            problems.append(f"{stage}: expected one {typ} {actor}->{target} {key} in tics {lo}-{hi}, found {len(hits)}")
        else:
            used.add(hits[0])
            found.append((stage, later[hits[0]]))
    extra = [e for i, e in enumerate(later) if i not in used]
    problems += [f"unexpected death after tic 0: {e}" for e in extra]
    return problems, found


def check_respawns(events, delay):
    problems, deaths = [], [e for e in events if e["type"] != "respawn"]
    want = delay * 35 + 1
    respawns = {(e["actor"], e["tic"]) for e in events if e["type"] == "respawn"}
    gaps = []
    for d in deaths:
        nxt = [t for s, t in respawns if s == d["target"] and t > d["tic"]]
        if not nxt:
            continue  # died too close to the end
        gaps.append(min(nxt) - d["tic"])
        if min(nxt) - d["tic"] != want:
            problems.append(f"slot {d['target']} died at {d['tic']}, respawned at {min(nxt)} (expected +{want})")
    return problems, gaps


def check_messages(capture, rows, notes_by_slot):
    """Static observers facing walls: slot 7 renders like a capture client, slot 8 renders messages.

    For every message printed after both observers have settled, each observer's top band during
    the message (up to 2 s) is compared with its own frame just before the message. Slot 8 must
    change, which shows where text would appear; slot 7 must not change at all.
    """
    problems, info = [], {}
    by = {(r["tic"], r["slot"]): r for r in rows}
    message_tics = sorted({n["tic"] for n in notes_by_slot[8]})
    frames, settle = {}, {}
    for s in OBSERVERS:
        frames[s] = np.load(os.path.join(capture["workdir"], f"slot{s}", "frames.npy"), mmap_mode="r")
        pose = [(by[(t, s)]["x"], by[(t, s)]["y"], by[(t, s)]["angle"]) for t in range(DURATION)]
        settle[s] = max(t for t in range(1, DURATION) if pose[t] != pose[t - 1]) + 1
        if not by[(DURATION - 1, s)]["alive"] or settle[s] > 300:
            problems.append(f"slot {s} was not alive and static from tic 300 (last moved at {settle[s] - 1})")
    start = max(settle.values()) + 70  # view height settles after the warp landing
    windows = [o for o in message_tics if o - DUMP_EVERY >= start]
    if not windows:
        return problems + ["no messages after the observers settled; positive control not exercised"], info
    for s in OBSERVERS:
        per_window, bbox = {}, None
        for o in windows:
            pre = frames[s][(o - 1) // DUMP_EVERY][:BAND_ROWS]
            worst = 0
            for i in range(o // DUMP_EVERY + 1, min(len(frames[s]), (o + 70) // DUMP_EVERY + 1)):
                diff = np.any(frames[s][i][:BAND_ROWS] != pre, axis=2)
                n = int(diff.sum())
                if n:
                    ys, xs = np.nonzero(diff)
                    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
                    bbox = box if bbox is None else (min(bbox[0], box[0]), min(bbox[1], box[1]),
                                                     max(bbox[2], box[2]), max(bbox[3], box[3]))
                worst = max(worst, n)
            per_window[o] = worst
        info[s] = {"settle_tic": settle[s], "max_changed_pixels_per_message": per_window, "bbox_xyxy": bbox}
        if s == 7 and any(per_window.values()):
            problems.append(f"capture-setting observer: top {BAND_ROWS} rows changed during messages "
                            f"{ {o: n for o, n in per_window.items() if n} }, bbox {bbox}")
        if s == 8:
            missing = [o for o, n in per_window.items() if not n]
            if missing:
                problems.append(f"positive control showed no text for messages at tics {missing}")
            if bbox and bbox[0] > 16:
                problems.append(f"positive-control text does not start at the left edge: bbox {bbox}")
    info["messages_checked"] = windows
    if info.get(8, {}).get("max_changed_pixels_per_message"):
        shown = info[8]["max_changed_pixels_per_message"]
        _save_evidence(frames[8], frames[7], capture["workdir"], max(shown, key=shown.get))
    return problems, info


def _save_evidence(on, off, workdir, tic):
    """Top band of the messages-on observer above the capture-setting observer, as a binary PPM."""
    i = (tic + 10) // DUMP_EVERY
    img = np.concatenate([on[i][:BAND_ROWS], np.full((4, on.shape[2], 3), 255, np.uint8), off[i][:BAND_ROWS]])
    with open(os.path.join(workdir, "message_area.ppm"), "wb") as f:
        f.write(f"P6 {img.shape[1]} {img.shape[0]} 255\n".encode())
        f.write(np.ascontiguousarray(img).tobytes())


def report(name, problems):
    print(f"{'PASS' if not problems else 'FAIL'}  {name}")
    for pr in problems[:10]:
        print(f"      {pr}")
    return not problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="Controlled bench: staged deaths with known answers.")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--port", type=int, default=5031)
    ap.add_argument("--out", default="out/bench")
    args = ap.parse_args(argv)
    err = preflight()
    if err:
        print(f"bench: refusing to run: {err}", file=sys.stderr)
        return 2
    iwad = extract_map.default_iwad()
    if sha256_file(iwad) != FREEDOOM_013_SHA256:
        print("bench: the staged teleporter needs MAP01 from Freedoom 0.13 (ViZDoom 1.3.2)", file=sys.stderr)
        return 2
    sheets = roster.load()
    map_data = extract_map.extract(iwad, "MAP01")
    plan = Geo(map_data, map_things(iwad, "MAP01")).plan()
    workdir = os.path.join(args.out, f"controlled-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-s{args.seed}")
    os.makedirs(workdir)
    with open(os.path.join(workdir, "plan.json"), "w") as f:
        json.dump(plan, f)
    print(f"controlled bench: {workdir}\nplan: {json.dumps(plan)}")
    try:
        capture = match.run_match(
            sheets=sheets, iwad=iwad, map_name="MAP01", seed=args.seed, duration_tics=DURATION,
            port=args.port, respawn_delay=RESPAWN_DELAY, workdir=workdir,
            controller="engine.bench.controlled:StagedController",
            controller_kwargs={s: {"plan": plan} for s in roster.SLOTS}, video=False, cheats=True,
            render_messages={8: True}, dump_frames_every={s: DUMP_EVERY for s in OBSERVERS}, log=lambda m: None)
    except match.MatchFailed as e:
        report("all 8 processes ran to the end", [str(e)])
        return 1
    data, problems, warnings = build(capture, DURATION, sheets)
    _, notes_by_slot, _ = bundle.read_capture(workdir)
    ok = report(f"capture: no gaps, mapping {data['numbers']}, frag vectors agree, events reconcile", problems)
    stage_problems, found = check_stages(data["events"])
    ok &= report("staged deaths attributed to the right players and keys", stage_problems)
    for stage, e in found:
        print(f"      {stage:<20} tic {e['tic']:>4}  {e['source']}")
    tic0 = [e for e in data["events"] if e["tic"] == 0]
    print(f"      tic-0 deaths: {len(tic0)} (spawn telefrags, uncredited)")
    resp_problems, gaps = check_respawns(data["events"], RESPAWN_DELAY)
    ok &= report(f"every death respawns after viz_respawn_delay {RESPAWN_DELAY}s (+{RESPAWN_DELAY * 35 + 1} tics), "
                 f"{len(gaps)} respawns", resp_problems)
    msg_problems, info = check_messages(capture, data["rows"], notes_by_slot)
    ok &= report("message rendering off: top-left message area clean in capture POV", msg_problems)
    print(f"      {json.dumps(info)}")
    print(f"      unknown notification lines: {len(data['unknown'])} (logged, no events)")
    print("CONTROLLED BENCH " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
