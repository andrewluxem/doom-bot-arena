"""Check a match bundle against docs/MATCH_FORMAT.md.

    python -m engine.validate_bundle bundles/<id>

Prints one line per check and exits 0 only if every check passes.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import bundle, game, obituaries, roster, weapons  # noqa: E402
from engine.geometry import MapGeometry  # noqa: E402

EVENT_TYPES = ("frag", "telefrag", "suicide", "environmental_death", "respawn")
TARGET_MEMORY_TICS = 35  # a bot may keep a target it lost sight of; one killed out of view stays up to 1 s
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Report:
    def __init__(self):
        self.results = []

    def check(self, name, problems):
        problems = [p for p in problems if p]
        self.results.append((name, problems))
        status = "PASS" if not problems else "FAIL"
        print(f"{status}  {name}")
        for p in problems[:10]:
            print(f"      {p}")
        if len(problems) > 10:
            print(f"      … {len(problems) - 10} more")

    @property
    def ok(self):
        return all(not p for _, p in self.results)


def check_match_json(m, map_data):
    p = []
    required = ["schema_version", "id", "date", "iwad", "map", "seed", "ticrate", "vizdoom_version", "platform",
                "duration_tics", "frame0_tic", "respawn_delay_s", "bots", "weapons_present", "engine", "score",
                "obituary_wad", "sync", "render", "host_args", "pov"]
    p += [f"missing field {k}" for k in required if k not in m]
    if p:
        return p
    if m["schema_version"] not in bundle.SCHEMA_VERSIONS:
        p.append(f"schema_version {m['schema_version']}")
    if m["schema_version"] >= 2:
        p += check_match_json_v2(m)
    if m["ticrate"] != game.TICRATE:
        p.append(f"ticrate {m['ticrate']}")
    if not (isinstance(m["duration_tics"], int) and m["duration_tics"] > 0):
        p.append(f"duration_tics {m['duration_tics']!r}")
    if not (isinstance(m["respawn_delay_s"], int) and m["respawn_delay_s"] >= 0):
        p.append(f"respawn_delay_s {m['respawn_delay_s']!r}")
    elif m["frame0_tic"] != bundle.frame0_tic(m["respawn_delay_s"], game.TICRATE):
        p.append(f"frame0_tic {m['frame0_tic']}, expected {bundle.frame0_tic(m['respawn_delay_s'], game.TICRATE)} "
                 f"for respawn_delay_s {m['respawn_delay_s']}")
    if game.version_tuple(m["vizdoom_version"]) < game.MIN_VERSION:
        p.append(f"vizdoom_version {m['vizdoom_version']} cannot capture events")
    if not SHA256.match(m["iwad"].get("sha256", "")):
        p.append("iwad.sha256 missing or malformed")
    if m["iwad"].get("sha256") != map_data["source"]["iwad_sha256"]:
        p.append("iwad.sha256 differs from map.json source.iwad_sha256 (map extracted from another IWAD)")
    if m["map"] != map_data["source"]["map"]:
        p.append(f"map {m['map']} but map.json is {map_data['source']['map']}")
    if not SHA256.match(m["obituary_wad"].get("sha256", "")):
        p.append("obituary_wad.sha256 missing or malformed")
    if m["obituary_wad"].get("keys") != len(obituaries.KEYS):
        p.append(f"obituary_wad.keys {m['obituary_wad'].get('keys')} != {len(obituaries.KEYS)}")
    if m["obituary_wad"].get("sha256") != hashlib.sha256(obituaries.wad_bytes()).hexdigest():
        p.append("obituary_wad.sha256 does not match the WAD engine/obituaries.py generates")
    sync = m["sync"]
    if sync.get("barrier") is not True or sync.get("gaps") != 0 or sync.get("frag_vector_mismatches") != 0:
        p.append(f"sync {sync}")
    if not p and sync.get("steps") != m["frame0_tic"] + m["duration_tics"]:
        p.append(f"sync.steps {sync.get('steps')} != frame0_tic + duration_tics")
    if any(m["render"].get("messages_per_slot", [True])):
        p.append(f"message rendering on for some slot: {m['render'].get('messages_per_slot')}")
    bots = m["bots"]
    if [b.get("slot") for b in bots] != list(roster.SLOTS):
        p.append(f"bots slots {[b.get('slot') for b in bots]}")
    numbers = [b.get("player_number") for b in bots]
    if sorted(numbers) != list(range(8)):
        p.append(f"player_number values {numbers} are not a permutation of 0-7")
    if [e["slot"] for e in m["engine"]] != list(roster.SLOTS):
        p.append("engine counts must list slots 1-8")
    elif [e["player_number"] for e in m["engine"]] != numbers:
        p.append("engine.player_number differs from bots.player_number")
    return p


def check_match_json_v2(m):
    """Schema 2 additions: who controlled each slot, its resolved weapons, the scoreboard setting."""
    p = []
    for b in m.get("bots", []):
        where = f"bots slot {b.get('slot')}"
        if not isinstance(b.get("controller"), str):
            p.append(f"{where}: controller missing")
        res = b.get("weapons_resolved", "missing")
        if res is not None and (not isinstance(res, list) or any(w not in weapons.NAMES for w in res)):
            p.append(f"{where}: weapons_resolved {res!r}")
    render = m.get("render", {})
    if render.get("scoreboard") is not False or any(render.get("scoreboard_per_slot", [True])):
        p.append(f"dead-player scoreboard on for some slot: {render.get('scoreboard_per_slot')}")
    return p


def check_rows(rows, n_tics, m=None, map_data=None):
    merged, p = bundle.merge_tics(_by_slot(rows), n_tics)
    if [(r["tic"], r["slot"]) for r in rows] != [(r["tic"], r["slot"]) for r in merged]:
        p.append("tics.jsonl is not sorted by (tic, slot)")
    version = (m or {}).get("schema_version", 1)
    fields = bundle.ROW_FIELDS[version]
    prev_alive = {}
    for r in rows:
        where = f"tic {r.get('tic')} slot {r.get('slot')}"
        if set(r) != fields:
            p.append(f"{where}: fields {sorted(set(r) ^ fields)}")
            continue
        if not all(isinstance(r[k], (int, float)) and not isinstance(r[k], bool) for k in ("x", "y", "angle")):
            p.append(f"{where}: x/y/angle not numeric")
        elif not 0 <= r["angle"] < 360:
            p.append(f"{where}: angle {r['angle']}")
        if not all(isinstance(r[k], int) and not isinstance(r[k], bool) for k in ("health", "armor", "weapon")):
            p.append(f"{where}: health/armor/weapon not int")
        elif not 0 <= r["weapon"] <= 9:
            p.append(f"{where}: weapon slot {r['weapon']}")
        if not isinstance(r["alive"], bool):
            p.append(f"{where}: alive not bool")
        if r["behavior"] not in roster.BEHAVIORS:
            p.append(f"{where}: behavior {r['behavior']!r}")
        if not r["alive"] and r["behavior"] != "DEAD":
            p.append(f"{where}: dead but behavior {r['behavior']}")
        if r["alive"] and r["behavior"] == "DEAD":
            p.append(f"{where}: alive but behavior DEAD")
        was = prev_alive.get(r["slot"])
        if r["behavior"] == "RESPAWNING" and was is not False:
            p.append(f"{where}: RESPAWNING without a death on the previous tic")
        if r["alive"] and was is False and r["behavior"] != "RESPAWNING":
            p.append(f"{where}: first alive tic after death is not RESPAWNING")
        u = r["utility_scores"]
        if u is not None and (not isinstance(u, dict) or any(k not in roster.BEHAVIORS for k in u)):
            p.append(f"{where}: utility_scores {u!r}")
        prev_alive[r["slot"]] = r["alive"]
    if version >= 2 and not p:
        p += check_rows_v2(rows, m, map_data)
    return p


def check_rows_v2(rows, m, map_data):
    """weapon_name, target_slot and sector (docs/MATCH_FORMAT.md section 5).

    The recorded sector is the engine's; it is re-derived from x, y and map.json and must agree
    except within 1.5 units of a sector boundary, where the two can legitimately differ.
    """
    p = []
    resolved = {b["slot"]: b.get("weapons_resolved") for b in m["bots"]}
    alive = {(r["tic"], r["slot"]): r["alive"] for r in rows}
    geo = MapGeometry(map_data)
    n_sectors = len(map_data["sectors"])
    checked = boundary = 0
    for r in rows:
        where = f"tic {r['tic']} slot {r['slot']}"
        w = r["weapon_name"]
        if w is not None and w not in weapons.NAMES:
            p.append(f"{where}: weapon_name {w!r}")
        elif w is not None and resolved[r["slot"]] is not None and w not in resolved[r["slot"]]:
            p.append(f"{where}: weapon_name {w} is not in the bot's weapons_resolved {resolved[r['slot']]}")
        tgt = r["target_slot"]
        if tgt is not None:
            if tgt not in roster.SLOTS or tgt == r["slot"]:
                p.append(f"{where}: target_slot {tgt!r}")
            elif not r["alive"]:
                p.append(f"{where}: dead with target_slot {tgt}")
            elif not any(alive.get((r["tic"] - k, tgt)) for k in range(TARGET_MEMORY_TICS + 1)):
                p.append(f"{where}: target_slot {tgt} was not alive in the last {TARGET_MEMORY_TICS} tics")
        sec = r["sector"]
        if sec is None:
            p.append(f"{where}: sector missing")
            continue
        if not (isinstance(sec, int) and not isinstance(sec, bool) and 0 <= sec < n_sectors):
            p.append(f"{where}: sector {sec!r}")
            continue
        checked += 1
        if sec not in geo.sectors_at(r["x"], r["y"]):
            if geo.near_sector_boundary(r["x"], r["y"]):
                boundary += 1
            else:
                p.append(f"{where}: sector {sec} but ({r['x']}, {r['y']}) lies in {geo.sectors_at(r['x'], r['y'])}")
    print(f"      sector cross-check: {checked} rows, {boundary} disagreements on a sector boundary (allowed)")
    return p


def check_spots(spots, m, n_tics):
    p = []
    modes = ("sightlines_max", "sightlines_min_near_traffic")
    if spots.get("map") != m["map"] or spots.get("iwad_sha256") != m["iwad"]["sha256"]:
        p.append("spots.json map or iwad_sha256 differs from match.json")
    ids = {}
    for mode, cands in spots.get("candidates", {}).items():
        if mode not in modes:
            p.append(f"unknown spot mode {mode}")
            continue
        for c in cands:
            if not all(isinstance(c.get(k), (int, float)) for k in ("id", "x", "y", "score", "watch_angle")):
                p.append(f"{mode} candidate {c}")
            ids[(mode, c.get("id"))] = c
    for c in spots.get("chosen", []):
        where = f"chosen {c}"
        if c.get("slot") not in roster.SLOTS or c.get("mode") not in modes:
            p.append(where)
        elif (c["mode"], c.get("spot")) not in ids:
            p.append(f"{where}: spot id not among the candidates")
        elif not (0 <= c.get("from_tic", -1) < n_tics) or (c.get("to_tic") is not None and not c["from_tic"] <= c["to_tic"] < n_tics):
            p.append(f"{where}: tic range")
    return p


def check_events(events, m, n_tics):
    p = []
    name_to_slot = {b["name"]: b["slot"] for b in m["bots"]}
    if [e.get("tic") for e in events] != sorted(e.get("tic") for e in events):
        p.append("events.jsonl is not sorted by tic")
    obit_lines = []
    for e in events:
        where = f"tic {e.get('tic')} {e.get('type')}"
        if set(e) != {"tic", "type", "actor", "target", "key", "credited", "source"}:
            p.append(f"{where}: fields {sorted(e)}")
            continue
        if e["type"] not in EVENT_TYPES:
            p.append(f"{where}: unknown type")
            continue
        if not (isinstance(e["tic"], int) and 0 <= e["tic"] < n_tics):
            p.append(f"{where}: tic out of range")
        if e["type"] == "respawn":
            if e["actor"] not in roster.SLOTS or e["target"] is not None:
                p.append(f"{where}: actor/target")
            if e["source"] != "tics.jsonl:alive false->true":
                p.append(f"{where}: source {e['source']!r}")
        else:
            obit_lines.append((e["tic"], e["source"]))
    rebuilt, warnings = bundle.death_events(obit_lines, name_to_slot)
    p += [f"event source does not re-parse: {w}" for w in warnings]
    stored = [e for e in events if e.get("type") in EVENT_TYPES and e["type"] != "respawn"]
    if not warnings and rebuilt != stored:
        diffs = [f"stored {a} != re-parsed {b}" for a, b in zip(stored, rebuilt) if a != b]
        p += diffs or ["death events differ from their re-parsed sources"]
    counts = {t: sum(1 for e in events if e.get("type") == t) for t in EVENT_TYPES}
    if m.get("event_counts") != counts:
        p.append(f"match.json event_counts {m.get('event_counts')} != {counts}")
    return p


def check_score(events, m):
    p = []
    if m["score"].get("rule") != bundle.SCORE_RULE:
        p.append(f"score rule {m['score'].get('rule')!r}")
    expected = bundle.score_rows(events, m["engine"], m["frame0_tic"])
    if m["score"].get("rows") != expected:
        p.append("score rows differ from the rule applied to events and engine counts")
    return p


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,r_frame_rate,pix_fmt,nb_read_frames:format=duration",
         "-of", "json", path], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"ffprobe exited {out.returncode}")
    data = json.loads(out.stdout)
    return data["streams"][0], float(data["format"]["duration"])


def check_video(path, n_tics):
    try:
        s, duration = ffprobe(path)
    except (OSError, RuntimeError, KeyError, IndexError, ValueError) as e:
        return [f"ffprobe failed: {e}"], None
    p = []
    want = {"codec_name": "h264", "width": game.WIDTH, "height": game.HEIGHT,
            "r_frame_rate": f"{game.TICRATE}/1", "pix_fmt": "yuv420p"}
    p += [f"{k} {s.get(k)!r}, expected {v!r}" for k, v in want.items() if s.get(k) != v]
    frames = int(s.get("nb_read_frames", -1))
    if frames != n_tics:
        p.append(f"decoded {frames} frames, expected {n_tics}")
    if abs(duration - n_tics / game.TICRATE) > 1.0 / game.TICRATE:
        p.append(f"duration {duration:.3f}s, expected {n_tics / game.TICRATE:.3f}s")
    return p, {"frames": frames, "duration_s": round(duration, 3), "bytes": os.path.getsize(path)}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _by_slot(rows):
    out = {}
    for r in rows:
        out.setdefault(r.get("slot"), []).append(r)
    return out


def validate(bundle_dir):
    rep = Report()
    files = ["match.json", "map.json", "tics.jsonl", "events.jsonl"] + [f"pov/slot{s}.mp4" for s in roster.SLOTS]
    m = None
    if os.path.isfile(os.path.join(bundle_dir, "match.json")):
        with open(os.path.join(bundle_dir, "match.json")) as f:
            m = json.load(f)
        if m.get("schema_version", 1) >= 2:
            files.append("spots.json")
    missing = [f for f in files if not os.path.isfile(os.path.join(bundle_dir, f))]
    rep.check("files present", [f"missing {f}" for f in missing])
    if missing:
        return rep, {}
    with open(os.path.join(bundle_dir, "map.json")) as f:
        map_data = json.load(f)
    rows = bundle.read_jsonl(os.path.join(bundle_dir, "tics.jsonl"))
    events = bundle.read_jsonl(os.path.join(bundle_dir, "events.jsonl"))

    rep.check("behavior vocabulary matches characters/README.md",
              [] if roster.readme_behaviors() == roster.BEHAVIORS else ["engine/roster.py BEHAVIORS drifted"])
    rep.check("match.json fields, IWAD sha256 matches map.json, sync, render", check_match_json(m, map_data))
    if not rep.ok:
        return rep, {}
    n, frame0 = m["duration_tics"], m["frame0_tic"]
    total = frame0 + n
    rep.check(f"tics.jsonl: {total} tics ({frame0} pre-roll + {n}) x 8 slots, no gaps or duplicates, field rules",
              check_rows(rows, total, m, map_data))
    if m["schema_version"] >= 2:
        with open(os.path.join(bundle_dir, "spots.json")) as f:
            rep.check("spots.json: candidates and chosen spots", check_spots(json.load(f), m, total))
    rep.check("events.jsonl: types, fields, sources re-parse to the same events", check_events(events, m, total))
    rep.check("deaths pair with telemetry; FRAGCOUNT and DEATHCOUNT reconcile",
              bundle.reconcile(events, rows, m["engine"]))
    rep.check(f"pre-roll: tic-0 deaths respawned by tic {bundle.respawn_tic(0, m['respawn_delay_s'], game.TICRATE)}",
              bundle.preroll_problems(rows, m["respawn_delay_s"], game.TICRATE))
    rep.check("score rows follow the stated rule", check_score(events, m))
    videos = {}
    pov_sha = {v.get("slot"): v.get("sha256") for v in m.get("pov", [])}
    for s in roster.SLOTS:
        path = os.path.join(bundle_dir, "pov", f"slot{s}.mp4")
        problems, info = check_video(path, n)
        if pov_sha.get(s) != _sha256(path):
            problems.append("sha256 differs from match.json pov entry")
        videos[s] = info
        rep.check(f"pov/slot{s}.mp4: h264 320x240 35fps, {n} frames from tic {frame0}", problems)
    return rep, videos


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle")
    args = ap.parse_args(argv)
    rep, videos = validate(args.bundle)
    for s, info in sorted(videos.items()):
        if info:
            print(f"      slot{s}: {info['frames']} frames, {info['duration_s']}s, {info['bytes']} bytes")
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(args.bundle) for f in fs)
    print(f"bundle size: {total} bytes ({total / 1e6:.1f} MB)")
    print("VALID" if rep.ok else "INVALID")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
