"""Start and supervise the eight player processes of one match."""

import json
import multiprocessing as mp
import os
import queue
import signal
import time

from engine import game, obituaries, roster

PLAYERS = 8


class MatchFailed(Exception):
    pass


def _kill_all(procs):
    for p in procs.values():
        try:
            os.killpg(p.pid, signal.SIGKILL)  # the slot process, its ViZDoom engine and its ffmpeg
        except (ProcessLookupError, PermissionError, TypeError):
            pass
        if p.is_alive():
            p.kill()
    for p in procs.values():
        p.join(5)


def _tail(path, n=8):
    try:
        with open(path) as f:
            return "".join(f.readlines()[-n:]).rstrip()
    except OSError:
        return "(no log)"


def run_match(*, sheets, iwad, map_name, seed, duration_tics, port, respawn_delay, workdir,
              controller="engine.bots:PlaceholderBot", controller_kwargs=None, video=True, video_from_tic=0,
              cheats=False, spawnfarthest=True, render_messages=None, dump_frames_every=None,
              startup_timeout=120.0, progress_timeout=60.0, barrier_timeout=60.0, log=print):
    """Run one match into workdir/slot<n>/. Returns run facts; raises MatchFailed after killing everything."""
    os.makedirs(workdir, exist_ok=True)
    wad = os.path.join(workdir, "obituaries.wad")
    wad_sha = obituaries.write_wad(wad)
    names = [s["name"] for s in sheets]
    obituaries.check_names(names)

    host = game.host_args(PLAYERS, port, respawn_delay, cheats=cheats, spawnfarthest=spawnfarthest)
    client = game.client_args(port)
    render_messages = render_messages or {}
    dump_frames_every = dump_frames_every or {}
    controller_kwargs = controller_kwargs or {}

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(PLAYERS)
    heartbeat = ctx.Array("i", [-1] * PLAYERS)
    ready = ctx.Queue()
    procs = {}
    specs = {}
    for sheet in sheets:
        slot = sheet["slot"]
        specs[slot] = {
            "slot": slot, "duration_tics": duration_tics, "iwad": iwad, "map": map_name, "seed": seed,
            "args": (host if slot == 1 else client) + game.player_args(sheet["name"], sheet["colorset"]),
            "wads": [wad], "render_messages": render_messages.get(slot, game.RENDER["messages"]),
            "controller": controller, "controller_kwargs": controller_kwargs.get(slot, {}),
            "sheet": sheet, "outdir": os.path.join(workdir, f"slot{slot}"), "video": video,
            "video_from_tic": video_from_tic,
            "dump_frames_every": dump_frames_every.get(slot), "barrier_timeout": barrier_timeout,
        }

    from engine.player import run_slot
    try:
        for slot in roster.SLOTS:
            p = ctx.Process(target=run_slot, args=(specs[slot], barrier, heartbeat, ready), name=f"slot{slot}")
            p.start()
            procs[slot] = p
            if slot == 1:
                time.sleep(1.0)  # the host must be listening before clients join

        started, deadline = set(), time.time() + startup_timeout
        while len(started) < PLAYERS:
            try:
                started.add(ready.get(timeout=0.5))
            except queue.Empty:
                pass
            dead = [s for s, p in procs.items() if p.exitcode is not None]
            if dead:
                raise MatchFailed(f"slot(s) {dead} exited during startup")
            if time.time() > deadline:
                raise MatchFailed(f"startup timeout ({startup_timeout:.0f}s): ready={sorted(started)}")
        log(f"all {PLAYERS} players in game")

        last_min, last_change, last_print = -1, time.time(), 0.0
        while True:
            codes = {s: p.exitcode for s, p in procs.items()}
            failed = {s: c for s, c in codes.items() if c not in (None, 0)}
            if failed:
                raise MatchFailed(f"slot exit codes {failed}")
            if all(c == 0 for c in codes.values()):
                break
            cur = min(heartbeat[:])
            now = time.time()
            if cur != last_min:
                last_min, last_change = cur, now
            elif now - last_change > progress_timeout:
                raise MatchFailed(f"no progress for {progress_timeout:.0f}s at tic {cur}")
            if now - last_print > 5:
                log(f"tic {cur + 1}/{duration_tics}")
                last_print = now
            time.sleep(0.2)
    except (MatchFailed, KeyboardInterrupt) as e:
        _kill_all(procs)
        details = "\n".join(f"  slot{s}: {_tail(os.path.join(specs[s]['outdir'], 'log.txt'), 3)}" for s in specs)
        raise MatchFailed(f"{e}\n{details}") from None

    metas = {}
    for slot in roster.SLOTS:
        with open(os.path.join(specs[slot]["outdir"], "meta.json")) as f:
            metas[slot] = json.load(f)
    return {"workdir": workdir, "obituary_wad_sha256": wad_sha, "obituary_wad": wad, "host_args": host,
            "client_args": client, "metas": metas, "render_messages": {s: specs[s]["render_messages"] for s in specs}}
