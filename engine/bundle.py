"""Turn per-slot capture output into bundle data, and the checks both the runner and validator use."""

import json
import os

from engine import obituaries, roster

SCHEMA_VERSION = 5  # 2: weapon_name, target_slot, sector, spots.json, per-bot controller; 3: damage counters; 4: attack;
# 5: the KITING behavior, match.json spot_traffic
SCHEMA_VERSIONS = (1, 2, 3, 4, 5)  # the validator still reads older bundles
ROW_FIELDS = {
    1: {"tic", "slot", "x", "y", "angle", "health", "armor", "weapon", "alive", "behavior", "utility_scores"},
}
ROW_FIELDS[2] = ROW_FIELDS[1] | {"weapon_name", "target_slot", "sector"}
ROW_FIELDS[3] = ROW_FIELDS[2] | {"hitcount", "damagecount", "damage_taken"}
ROW_FIELDS[4] = ROW_FIELDS[3] | {"attack"}
ROW_FIELDS[5] = ROW_FIELDS[4]
SCORE_RULE = ("displayed columns count events at tic >= frame0_tic; displayed_frags = kills - suicides; "
              "displayed_deaths = deaths; frags_raw and deaths_raw are engine counters at the last tic")


def read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, items):
    with open(path, "w") as f:
        for it in items:
            f.write(json.dumps(it, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------ pre-roll
def respawn_tic(death_tic, respawn_delay_s, ticrate):
    """A death at tic d respawns at d + ticrate * delay + 1 (checked by the controlled bench)."""
    return death_tic + ticrate * respawn_delay_s + 1


def frame0_tic(respawn_delay_s, ticrate):
    """First displayed tic: the tic-0 deaths have respawned, plus a margin of the respawn delay + 1 s."""
    return respawn_tic(0, respawn_delay_s, ticrate) + ticrate * respawn_delay_s + ticrate


def preroll_problems(rows, respawn_delay_s, ticrate):
    """Every slot dead at tic 0 must be alive again by the respawn tic that frame0_tic is counted from."""
    by = respawn_tic(0, respawn_delay_s, ticrate)
    dead_at_0, first_alive = set(), {}
    for r in rows:
        if r["tic"] == 0 and not r["alive"]:
            dead_at_0.add(r["slot"])
        if r["alive"]:
            first_alive.setdefault(r["slot"], r["tic"])
    return [f"slot {s} dead at tic 0 first alive at tic {first_alive.get(s)}, expected by tic {by}"
            for s in sorted(dead_at_0) if first_alive.get(s, by + 1) > by]


# ------------------------------------------------------------------ capture -> bundle
def slot_problems(metas, n_tics):
    problems = []
    firsts = {m.get("first_episode_tic") for m in metas.values()}
    if len(firsts) != 1:
        problems.append(f"processes started at different engine tics: {sorted(firsts, key=str)}")
    numbers = {}
    for slot, m in sorted(metas.items()):
        if m.get("error"):
            problems.append(f"slot {slot}: {m['error']}")
        if m.get("steps") != n_tics:
            problems.append(f"slot {slot}: {m.get('steps')} steps, expected {n_tics}")
        if m.get("gaps"):
            problems.append(f"slot {slot}: {m['gaps']} tic gap(s)")
        pn = m.get("player_numbers", [])
        if len(pn) != 1:
            problems.append(f"slot {slot}: PLAYER_NUMBER not constant: {pn}")
        else:
            numbers[slot] = pn[0]
    if len(set(numbers.values())) != len(numbers):
        problems.append(f"PLAYER_NUMBER not distinct across slots: {numbers}")
    return problems, numbers


def merge_tics(rows_by_slot, n_tics):
    """Merge per-slot rows; reject missing rows, duplicates, invalid slots and inconsistent ranges."""
    problems, merged = [], []
    for slot, rows in sorted(rows_by_slot.items()):
        if slot not in roster.SLOTS:
            problems.append(f"invalid slot {slot}")
        if any(r["slot"] != slot for r in rows):
            problems.append(f"slot {slot}: rows owned by another slot")
        tics = [r["tic"] for r in rows]
        if tics != list(range(n_tics)):
            dup = len(tics) - len(set(tics))
            missing = n_tics - len(set(tics) & set(range(n_tics)))
            problems.append(f"slot {slot}: tic range {tics[:1]}..{tics[-1:]} n={len(tics)} "
                            f"duplicates={dup} missing={missing}")
        merged.extend(rows)
    merged.sort(key=lambda r: (r["tic"], r["slot"]))
    return merged, problems


def frag_vector_mismatches(fragvecs_by_slot):
    by_tic = {}
    for vecs in fragvecs_by_slot.values():
        for v in vecs:
            by_tic.setdefault(v[0], set()).add(tuple(v[1:]))
    return sorted(t for t, s in by_tic.items() if len(s) > 1)


def agreed_obituary_lines(notes_by_slot):
    """OBIT lines must be identical, in the same order and tics, in every process.

    Returns (lines, unknown, problems): lines = [(tic, raw)], unknown = [(slot, tic, raw)].
    """
    per_slot, unknown = {}, []
    for slot, notes in sorted(notes_by_slot.items()):
        per_slot[slot] = [(n["tic"], n["line"]) for n in notes if n["line"].startswith(obituaries.PREFIX)]
        unknown += [(slot, n["tic"], n["line"]) for n in notes if not n["line"].startswith(obituaries.PREFIX)]
    reference = per_slot[min(per_slot)]
    problems = [f"slot {s} saw different obituaries than slot {min(per_slot)}"
                for s, lines in per_slot.items() if lines != reference]
    return reference, unknown, problems


def death_events(lines, name_to_slot):
    """Obituary lines -> events. Lines that do not parse or attribute become warnings, never events."""
    events, warnings = [], []
    names = set(name_to_slot)
    for tic, raw in lines:
        ob, warn = obituaries.parse(raw, names)
        if ob is None:
            warnings.append(f"tic {tic}: {warn}")
            continue
        death, warn = obituaries.classify(ob)
        if death is None:
            warnings.append(f"tic {tic}: {warn}")
            continue
        events.append({
            "tic": tic, "type": death.type,
            "actor": name_to_slot[death.actor] if death.actor else None,
            "target": name_to_slot[death.target],
            "key": death.key,
            # The engine applies no frag change while level.maptime == 0 (spawning at level start).
            "credited": tic > 0 if death.type != "environmental_death" else None,
            "source": raw,
        })
    return events, warnings


def respawn_events(rows):
    events, prev = [], {}
    for r in rows:
        if prev.get(r["slot"]) is False and r["alive"]:
            events.append({"tic": r["tic"], "type": "respawn", "actor": r["slot"], "target": None,
                           "key": None, "credited": None, "source": "tics.jsonl:alive false->true"})
        prev[r["slot"]] = r["alive"]
    return events


def sort_events(events):
    order = {"frag": 0, "telefrag": 0, "suicide": 0, "environmental_death": 0, "respawn": 1}
    return sorted(events, key=lambda e: (e["tic"], order[e["type"]], e["target"] or e["actor"]))


# ------------------------------------------------------------------ scoring and reconciliation
def score_rows(events, engine, frame0):
    """Displayed columns cover the displayed match only (tic >= frame0); pre-roll events stay in events.jsonl."""
    rows = []
    shown = [x for x in events if x["type"] != "respawn" and x["tic"] >= frame0]
    for e in engine:
        slot = e["slot"]
        kills = sum(1 for x in shown if x["type"] == "frag" and x["actor"] == slot and x["credited"])
        suicides = sum(1 for x in shown if x["type"] == "suicide" and x["target"] == slot and x["credited"])
        deaths = [x for x in shown if x["target"] == slot]
        rows.append({
            "slot": slot,
            "displayed_frags": kills - suicides,
            "displayed_deaths": len(deaths),
            "frags_raw": e["fragcount"],
            "deaths_raw": e["deathcount"],
            "kills": kills,
            "telefrags": sum(1 for x in shown if x["type"] == "telefrag" and x["actor"] == slot and x["credited"]),
            "suicides": suicides,
            "environmental_deaths": sum(1 for x in deaths if x["type"] == "environmental_death"),
        })
    return rows


def reconcile(events, rows, engine):
    """Engine counters verify the obituary-derived events; they never create them."""
    problems = []
    deaths = [e for e in events if e["type"] != "respawn"]
    for e in engine:
        slot = e["slot"]
        expected_frags = (sum(1 for x in deaths if x["actor"] == slot and x["type"] in ("frag", "telefrag") and x["credited"])
                          - sum(1 for x in deaths if x["type"] == "suicide" and x["target"] == slot and x["credited"]))
        if expected_frags != e["fragcount"]:
            problems.append(f"slot {slot}: engine FRAGCOUNT {e['fragcount']} != events {expected_frags}")
        later = sum(1 for x in deaths if x["target"] == slot and x["tic"] > 0)
        if later != e["deathcount"]:
            problems.append(f"slot {slot}: engine DEATHCOUNT {e['deathcount']} != deaths after tic 0 {later}")

    by_slot = {}
    for r in rows:
        by_slot.setdefault(r["slot"], []).append(r)
    death_at = {}
    for x in deaths:
        death_at.setdefault((x["tic"], x["target"]), []).append(x)
    flips = set()
    for slot, rs in by_slot.items():
        if not rs[0]["alive"]:
            flips.add((0, slot))
        for a, b in zip(rs, rs[1:]):
            if a["alive"] and not b["alive"]:
                flips.add((b["tic"], slot))
    for key in sorted(flips):
        n = len(death_at.get(key, []))
        if n != 1:
            problems.append(f"slot {key[1]} died at tic {key[0]} with {n} matching obituary events")
    for key in sorted(set(death_at) - flips):
        problems.append(f"obituary at tic {key[0]} for slot {key[1]} without a death in its telemetry")
    resp = {(e["tic"], e["actor"]) for e in events if e["type"] == "respawn"}
    expected_resp = {(e["tic"], e["actor"]) for e in respawn_events(rows)}
    if resp != expected_resp:
        problems.append(f"respawn events differ from telemetry: {sorted(resp ^ expected_resp)[:5]}")
    return problems


def write_tics(path, rows):
    write_jsonl(path, rows)


def read_capture(workdir):
    rows, notes, fragvecs = {}, {}, {}
    for slot in roster.SLOTS:
        d = os.path.join(workdir, f"slot{slot}")
        rows[slot] = read_jsonl(os.path.join(d, "rows.jsonl"))
        notes[slot] = read_jsonl(os.path.join(d, "notifications.jsonl"))
        fragvecs[slot] = read_jsonl(os.path.join(d, "fragvec.jsonl"))
    return rows, notes, fragvecs
