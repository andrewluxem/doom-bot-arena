"""One player process: one slot, one tic per loop, its own telemetry and video.

Started by engine/match.py with the spawn start method. The process puts
itself in a new session so the supervisor can kill it together with its
ViZDoom engine and ffmpeg children.
"""

import importlib
import json
import math
import os
import subprocess
import sys
import time
import traceback

from engine import game, roster

EXIT_ERROR, EXIT_GAP = 2, 3


class SlotFailure(Exception):
    pass


def load_controller(path, **kwargs):
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)(**kwargs)


def ffmpeg_cmd(path):
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{game.WIDTH}x{game.HEIGHT}",
            "-r", str(game.TICRATE), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-g", str(game.TICRATE), "-r", str(game.TICRATE), "-movflags", "+faststart", path]


def run_slot(spec, barrier, heartbeat, ready):
    try:
        os.setsid()
    except OSError:
        pass
    slot = spec["slot"]
    out = spec["outdir"]
    os.makedirs(out, exist_ok=True)
    log = open(os.path.join(out, "log.txt"), "a", buffering=1)
    meta = {"slot": slot, "vizdoom_version": game.version(), "steps": 0, "gaps": 0}
    try:
        _run(spec, barrier, heartbeat, ready, log, meta, out)
        code = 0
    except SlotFailure as e:
        log.write(f"FAIL: {e}\n")
        meta["error"] = str(e)
        code = EXIT_GAP if meta["gaps"] else EXIT_ERROR
    except Exception as e:  # noqa: BLE001 - every failure must end the match
        log.write(traceback.format_exc())
        meta["error"] = f"{type(e).__name__}: {e}"
        code = EXIT_ERROR
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    log.close()
    sys.stdout.flush()
    os._exit(code)


def _run(spec, barrier, heartbeat, ready, log, meta, out):
    slot, n_tics = spec["slot"], spec["duration_tics"]
    sheet = spec["sheet"]
    g = game.make_game(iwad=spec["iwad"], map_name=spec["map"], seed=spec["seed"], args=spec["args"],
                       wads=spec["wads"], render_messages=spec["render_messages"])
    t0 = time.time()
    g.init()
    log.write(f"init ok in {time.time() - t0:.1f}s\n")
    ready.put(slot)

    ff = None
    if spec["video"]:
        ff_log = open(os.path.join(out, "ffmpeg.log"), "w")
        ff = subprocess.Popen(ffmpeg_cmd(os.path.join(out, "pov.mp4")), stdin=subprocess.PIPE, stderr=ff_log)
        meta["ffmpeg_pid"] = ff.pid
    dump = None
    if spec.get("dump_frames_every"):
        import numpy as np
        k = spec["dump_frames_every"]
        dump = np.lib.format.open_memmap(os.path.join(out, "frames.npy"), mode="w+", dtype=np.uint8,
                                         shape=(math.ceil(n_tics / k), game.HEIGHT, game.WIDTH, 3))

    ctrl = load_controller(spec["controller"], slot=slot, sheet=sheet, seed=spec["seed"],
                           **spec.get("controller_kwargs", {}))
    rows = open(os.path.join(out, "rows.jsonl"), "w")
    notes = open(os.path.join(out, "notifications.jsonl"), "w")
    fragvec = open(os.path.join(out, "fragvec.jsonl"), "w")
    player_numbers = set()
    prev_alive = None
    first_et = None
    v = None
    for t in range(n_tics):
        if g.is_episode_finished():
            raise SlotFailure(f"episode finished at bundle tic {t}")
        st = g.get_state()
        if st is None:
            raise SlotFailure(f"no state at bundle tic {t}")
        et = g.get_episode_time()
        if first_et is None:
            first_et = meta["first_episode_tic"] = et
        if et != first_et + t:
            meta["gaps"] += 1
            raise SlotFailure(f"engine tic {et} at bundle tic {t}: expected {first_et + t}")
        v = dict(zip(game.VARIABLES, (float(x) for x in st.game_variables)))
        player_numbers.add(int(v["PLAYER_NUMBER"]))
        alive = v["DEAD"] == 0

        action, cmds, behavior, utility = ctrl.act(t, st, v)
        if not alive:
            behavior, action, cmds = "DEAD", {}, []
        elif prev_alive is False:
            behavior = "RESPAWNING"
        if behavior not in roster.BEHAVIORS:
            raise SlotFailure(f"controller returned behavior {behavior!r} at tic {t}")
        prev_alive = alive

        rows.write(json.dumps({
            "tic": t, "slot": slot,
            "x": round(v["POSITION_X"], 3), "y": round(v["POSITION_Y"], 3), "angle": round(v["ANGLE"], 3),
            "health": int(v["HEALTH"]), "armor": int(v["ARMOR"]), "weapon": int(v["SELECTED_WEAPON"]),
            "alive": alive, "behavior": behavior, "utility_scores": utility,
        }) + "\n")
        fragvec.write(json.dumps([t] + [int(v[f"PLAYER{i}_FRAGCOUNT"]) for i in range(1, 9)]) + "\n")
        for line in (st.notifications_buffer or "").splitlines():
            if line.strip():
                notes.write(json.dumps({"tic": t, "line": line}) + "\n")

        frame = st.screen_buffer
        if t == 0:
            meta["frame_shape"] = list(frame.shape)
            if tuple(frame.shape) != (game.HEIGHT, game.WIDTH, 3):
                raise SlotFailure(f"screen buffer shape {frame.shape}, expected (240, 320, 3)")
        if ff is not None and t >= spec["video_from_tic"]:  # pre-roll tics get rows, not frames
            try:
                ff.stdin.write(frame.tobytes())
            except BrokenPipeError:
                raise SlotFailure(f"ffmpeg exited early (rc={ff.poll()}) at tic {t}")
        if dump is not None and t % spec["dump_frames_every"] == 0:
            dump[t // spec["dump_frames_every"]] = frame

        heartbeat[slot - 1] = t
        meta["steps"] = t + 1
        if t < n_tics - 1:
            for c in cmds:
                g.send_game_command(c)
            g.make_action(game.action_vector(action), 1)
            barrier.wait(spec["barrier_timeout"])

    rows.close()
    notes.close()
    fragvec.close()
    if dump is not None:
        dump.flush()
    if ff is not None:
        ff.stdin.close()
        rc = ff.wait(timeout=300)
        meta["ffmpeg_rc"] = rc
        if rc != 0:
            raise SlotFailure(f"ffmpeg exited with {rc}")
    meta["player_numbers"] = sorted(player_numbers)
    meta["final"] = {"FRAGCOUNT": int(v["FRAGCOUNT"]), "DEATHCOUNT": int(v["DEATHCOUNT"]),
                     "PLAYER_FRAGCOUNT": [int(v[f"PLAYER{i}_FRAGCOUNT"]) for i in range(1, 9)]}
    barrier.wait(spec["barrier_timeout"])  # everyone has finished recording
    g.close()
