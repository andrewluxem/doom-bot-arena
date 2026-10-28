"""Host one 8-bot deathmatch and write a match bundle (docs/MATCH_FORMAT.md).

    python -m engine.runner --duration-tics 2100 --seed 1234

--duration-tics is the displayed match. The capture runs frame0_tic pre-roll tics
first (bundle.frame0_tic); they get telemetry rows but no video frames.

Capture output (per-slot logs, raw notifications) goes to <out>/.capture/<id>/.
The bundle is written to <out>/<id>/ only if every process finished and every
check passed. Exit codes: 0 bundle written, 1 a process failed (all killed),
2 refused before starting, 4 capture finished but failed its checks.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import bundle, game, match, obituaries, roster, weapons  # noqa: E402
from tools import extract_map  # noqa: E402

PLACEHOLDER = "engine.bots:PlaceholderBot"
PERSONALITY = "engine.personality.controller:PersonalityBot"
MILESTONE_2A = "VEX,HEX,NUL"  # the 2A trio; since milestone 2B all eight are personalities by default


def controllers(sheets, codes, map_data, cache_dir):
    """Controller path and kwargs per slot: the personality engine for `codes`, placeholders otherwise."""
    paths, kwargs, resolved = {}, {}, {}
    for sh in sheets:
        slot = sh["slot"]
        if sh["code"] in codes:
            paths[slot] = PERSONALITY
            kwargs[slot] = {"map_data": map_data, "cache_dir": cache_dir}
            resolved[slot] = weapons.resolve(sh["weapons"]["allowed"], sh["weapons"]["weights"],
                                             map_data["weapons_present"])
        else:
            paths[slot] = PLACEHOLDER
            kwargs[slot] = {"map_data": map_data}
            resolved[slot] = None  # placeholders do not follow their sheet's weapons
    return paths, kwargs, resolved


def spots_json(capture_dir, map_data, sheets, tables):
    """Spot candidates per mode and every spot a bot chose (written by its controller), for spots.json."""
    from engine.personality import spots
    chosen = []
    for sh in sheets:
        path = os.path.join(capture_dir, f"slot{sh['slot']}", "spots_chosen.json")
        if os.path.isfile(path):
            with open(path) as f:
                chosen += json.load(f)
    chosen.sort(key=lambda c: (c["from_tic"], c["slot"]))
    params, candidates = [], {}
    for t in tables.values():
        params += t["params"]
        candidates.update(t["candidates"])
    return {"spots_version": spots.VERSION, "map": map_data["source"]["map"],
            "iwad_sha256": map_data["source"]["iwad_sha256"], "params": params,
            "candidates": candidates, "chosen": chosen}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tool_version(cmd):
    try:
        return subprocess.run([cmd, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError):
        return None


def preflight():
    err = game.check_version()
    if err:
        return err
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            return f"{tool} not found on PATH"
    return None


def build(capture, n_tics, sheets, respawn_delay=None):
    """Checks and bundle data from a finished capture. Returns (data, problems, warnings).

    n_tics counts every captured tic. With respawn_delay, the capture also has to satisfy the pre-roll.
    """
    metas = capture["metas"]
    problems, numbers = bundle.slot_problems(metas, n_tics)
    rows_by_slot, notes_by_slot, fragvecs = bundle.read_capture(capture["workdir"])
    rows, p = bundle.merge_tics(rows_by_slot, n_tics)
    problems += p
    mismatches = bundle.frag_vector_mismatches(fragvecs)
    if mismatches:
        problems.append(f"frag vectors differ across processes at {len(mismatches)} tics, first {mismatches[:5]}")
    lines, unknown, p = bundle.agreed_obituary_lines(notes_by_slot)
    problems += p
    name_to_slot = {s["name"]: s["slot"] for s in sheets}
    deaths, warnings = bundle.death_events(lines, name_to_slot)
    for slot, tic, raw in unknown:
        warnings.append(f"slot {slot} tic {tic}: unknown notification (no event): {raw!r}")
    events = bundle.sort_events(deaths + bundle.respawn_events(rows))
    engine = [{"slot": s, "player_number": numbers.get(s), "fragcount": metas[s]["final"]["FRAGCOUNT"],
               "deathcount": metas[s]["final"]["DEATHCOUNT"]} for s in roster.SLOTS if "final" in metas[s]]
    for s in roster.SLOTS:
        if s in numbers and "final" in metas[s]:
            vec = metas[s]["final"]["PLAYER_FRAGCOUNT"][numbers[s]]
            if vec != metas[s]["final"]["FRAGCOUNT"]:
                problems.append(f"slot {s}: PLAYER{numbers[s] + 1}_FRAGCOUNT {vec} != own FRAGCOUNT")
    problems += bundle.reconcile(events, rows, engine)
    if respawn_delay is not None:
        problems += bundle.preroll_problems(rows, respawn_delay, game.TICRATE)
    data = {"rows": rows, "events": events, "engine": engine, "numbers": numbers,
            "mismatches": mismatches, "unknown": unknown, "obituary_lines": lines,
            "unattributed": len(lines) - len(deaths)}
    return data, problems, warnings


def main(argv=None):
    p = argparse.ArgumentParser(description="Host one 8-bot ViZDoom deathmatch and write a match bundle.")
    p.add_argument("--iwad", help="IWAD path (default: the Freedoom IWAD shipped with ViZDoom, freedoom1 for ExMy maps)")
    p.add_argument("--map", default="MAP01")
    p.add_argument("--duration-tics", type=int, default=10500,
                   help="displayed match, after the pre-roll; default 10500 = 5 minutes")
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--port", type=int, default=5029)
    p.add_argument("--respawn-delay", type=int, default=2, help="viz_respawn_delay, seconds, server-wide")
    p.add_argument("--out", default="bundles")
    p.add_argument("--startup-timeout", type=float, default=120)
    p.add_argument("--progress-timeout", type=float, default=60)
    p.add_argument("--personalities", default="all",
                   help="codes driven by the personality engine, or 'all' or 'none'; the rest are placeholders "
                        "(default %(default)s; 'none' is the separation baseline)")
    args = p.parse_args(argv)

    err = preflight()
    if err:
        print(f"runner: refusing to run: {err}", file=sys.stderr)
        return 2
    sheets = roster.load()
    iwad = os.path.abspath(args.iwad or extract_map.default_iwad(args.map))
    try:
        map_data = extract_map.extract(iwad, args.map)
    except (OSError, extract_map.WadError) as e:
        print(f"runner: refusing to run: {e}", file=sys.stderr)
        return 2

    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    match_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{args.map.upper()}-s{args.seed}"
    capture_dir = os.path.join(args.out, ".capture", match_id)
    bundle_dir = os.path.join(args.out, match_id)
    os.makedirs(capture_dir)
    log_file = open(os.path.join(capture_dir, "runner.log"), "w", buffering=1)
    spec = args.personalities.strip().lower()
    codes = ({sh["code"] for sh in sheets} if spec == "all" else set() if spec == "none"
             else {c.strip().upper() for c in args.personalities.split(",") if c.strip()})
    unknown = codes - {sh["code"] for sh in sheets}
    if unknown:
        print(f"runner: refusing to run: unknown personality codes {sorted(unknown)}", file=sys.stderr)
        return 2
    cache_dir = os.path.join(args.out, ".cache")
    paths, ctrl_kwargs, resolved = controllers(sheets, codes, map_data, cache_dir)
    from engine import personality
    _, spot_tables = personality.prepare(map_data, cache_dir, [sh for sh in sheets if sh["code"] in codes])

    def log(msg):
        print(msg, flush=True)
        log_file.write(msg + "\n")

    frame0 = bundle.frame0_tic(args.respawn_delay, game.TICRATE)
    total = frame0 + args.duration_tics
    log(f"match {match_id}: ViZDoom {game.version()}, {frame0} pre-roll + {args.duration_tics} tics, seed {args.seed}")
    try:
        capture = match.run_match(
            sheets=sheets, iwad=iwad, map_name=args.map.upper(), seed=args.seed,
            duration_tics=total, video_from_tic=frame0, port=args.port, respawn_delay=args.respawn_delay,
            workdir=capture_dir, controller=paths, controller_kwargs=ctrl_kwargs, startup_timeout=args.startup_timeout,
            progress_timeout=args.progress_timeout, log=log)
    except match.MatchFailed as e:
        log(f"FAILED: {e}")
        return 1

    data, problems, warnings = build(capture, total, sheets, respawn_delay=args.respawn_delay)
    for w in warnings:
        log_file.write(f"warning: {w}\n")
    if warnings:
        log(f"{len(warnings)} warning(s) (unknown notifications or unattributable obituaries), "
            f"none turned into events; see {log_file.name}")
    if problems:
        for pr in problems:
            log(f"check failed: {pr}")
        log(f"no bundle written; capture kept in {capture_dir}")
        return 4

    os.makedirs(os.path.join(bundle_dir, "pov"))
    for s in roster.SLOTS:
        shutil.move(os.path.join(capture_dir, f"slot{s}", "pov.mp4"), os.path.join(bundle_dir, "pov", f"slot{s}.mp4"))
    bundle.write_tics(os.path.join(bundle_dir, "tics.jsonl"), data["rows"])
    bundle.write_jsonl(os.path.join(bundle_dir, "events.jsonl"), data["events"])
    extract_map.write_map_json(map_data, os.path.join(bundle_dir, "map.json"))
    with open(os.path.join(bundle_dir, "spots.json"), "w") as f:
        json.dump(spots_json(capture_dir, map_data, sheets, spot_tables), f, separators=(",", ":"))
        f.write("\n")

    by_slot = {s["slot"]: s for s in sheets}
    match_json = {
        "schema_version": bundle.SCHEMA_VERSION,
        "id": match_id,
        "date": started.isoformat().replace("+00:00", "Z"),
        "iwad": {"name": os.path.basename(iwad), "sha256": sha256_file(iwad)},
        "map": args.map.upper(),
        "seed": args.seed,
        "ticrate": game.TICRATE,
        "vizdoom_version": game.version(),
        "platform": {"os": platform.platform(), "python": platform.python_version(),
                     "ffmpeg": tool_version("ffmpeg")},
        "duration_tics": args.duration_tics,
        "frame0_tic": frame0,
        "respawn_delay_s": args.respawn_delay,
        "skill": game.SKILL,
        "bots": [{**roster.identity(by_slot[s]), "player_number": data["numbers"][s], "controller": paths[s],
                  "weapons_resolved": resolved[s]} for s in roster.SLOTS],
        "weapons_present": map_data["weapons_present"],
        "engine": data["engine"],
        "score": {"rule": bundle.SCORE_RULE, "rows": bundle.score_rows(data["events"], data["engine"], frame0)},
        "event_counts": {t: sum(1 for e in data["events"] if e["type"] == t)
                         for t in ("frag", "telefrag", "suicide", "environmental_death", "respawn")},
        "obituary_wad": {"sha256": capture["obituary_wad_sha256"], "keys": len(obituaries.KEYS),
                         "format": "OBIT|<key>|<victim>|<killer>"},
        "notifications": {"unknown_lines": len(data["unknown"]), "unattributed_obituaries": data["unattributed"]},
        "sync": {"barrier": True, "steps": total,
                 "gaps": sum(m["gaps"] for m in capture["metas"].values()),
                 "frag_vector_mismatches": len(data["mismatches"])},
        "render": {**game.RENDER, "messages_per_slot": [capture["render_messages"][s] for s in roster.SLOTS],
                   "scoreboard_per_slot": [capture["scoreboard"][s] for s in roster.SLOTS]},
        "pov": [{"slot": s, "path": f"pov/slot{s}.mp4", "sha256": sha256_file(os.path.join(bundle_dir, "pov", f"slot{s}.mp4"))}
                for s in roster.SLOTS],
        "host_args": capture["host_args"],
        "client_args": capture["client_args"],
    }
    with open(os.path.join(bundle_dir, "match.json"), "w") as f:
        json.dump(match_json, f, indent=1)
        f.write("\n")
    log(f"bundle written: {bundle_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
