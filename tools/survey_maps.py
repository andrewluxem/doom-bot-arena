"""Survey deathmatch maps for an evaluation: starts, weapons, and whether key pickups are reachable.

    python3 tools/survey_maps.py [--iwad freedoom2.wad --maps MAP01-MAP10] [--json out.json]

For each map: deathmatch starts, weapons present in deathmatch, item counts, and for the
chainsaw and the berserk pack whether the map has one and from how many of the deathmatch
starts it can be reached without riding a lift, and with one. Reachability uses the personality engine's nav
graph (engine/personality/nav.py): walking, doors, teleporters, and doors opened from
elsewhere treated as openable. Key-locked doors are walls in deathmatch. Switch-raised
stairs, crushers and damaging floors are not modelled, so 'reachable' is optimistic there.

A pickup counts as reachable from a nav node when a player walking straight from the node
toward it can touch it (Doom's rule: boxes overlap, so within 16 + 20 units on both axes,
and the item's floor is from 8 below to 56 above the player's). That covers items in
alcoves too low to enter. The nearest node alone would not: it can be behind a wall.
"""

import argparse
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.personality import nav  # noqa: E402
from tools import extract_map  # noqa: E402

KEY_PICKUPS = {"chainsaw": ("weapons", "chainsaw"), "berserk": ("items", "Berserk")}
TIMED = {"Stimpack", "Medikit", "Soulsphere", "GreenArmor", "BlueArmor", "Megasphere"}
TOUCH = 16 + 20  # player radius + weapon/item radius
SEARCH = 128


def touch_nodes(g, x, y):
    """Nav nodes from which a player walking straight toward (x, y) can pick up a thing there."""
    item_sector = g.geo.sector_at(x, y)
    if item_sector is None:
        return set()
    item_floor = g.floor[item_sector]
    out = set()
    for i in g.node_grid.query_box(x - SEARCH, y - SEARCH, x + SEARCH, y + SEARCH):
        nx, ny, ns = g.nodes[i]
        lo, hi = 0.0, 1.0  # furthest walkable fraction of the way from the node to the item
        if g.move_check((nx, ny), (x, y), sp=ns) is not None:
            lo = 1.0
        else:
            for _ in range(10):
                mid = (lo + hi) / 2
                if g.move_check((nx, ny), (nx + (x - nx) * mid, ny + (y - ny) * mid), sp=ns) is not None:
                    lo = mid
                else:
                    hi = mid
        qx, qy = nx + (x - nx) * lo, ny + (y - ny) * lo
        qs = g.geo.sector_at(qx, qy)
        if qs is None or max(abs(qx - x), abs(qy - y)) > TOUCH:
            continue
        if -8 <= item_floor - g.floor[qs] <= 56:
            out.add(i)
    return out


def map_names(spec):
    if "-" not in spec:
        return [spec]
    a, b = spec.split("-")
    if a.startswith("MAP"):
        return [f"MAP{i:02d}" for i in range(int(a[3:]), int(b[3:]) + 1)]
    return [f"E{a[1]}M{i}" for i in range(int(a[3:]), int(b[3:]) + 1)]


def survey(iwad, name):
    m = extract_map.extract(iwad, name)
    g = nav.NavGraph(m)
    live_open = {s: g.door_open_ceiling[s] for s in g.remote_doors}
    starts = [g.nearest(s["x"], s["y"], max_dist=96, need_clear=False) for s in m["deathmatch_starts"]]
    no_lift = [set(g.dijkstra(st, allow=nav.USABLE_KINDS - {"lift"}, live=live_open)[0]) if st is not None else set()
               for st in starts]
    with_lift = [set(g.dijkstra(st, allow=nav.USABLE_KINDS, live=live_open)[0]) if st is not None else set()
                 for st in starts]
    out = {"map": name, "deathmatch_starts": len(m["deathmatch_starts"]), "weapons_present": m["weapons_present"],
           "nav_nodes": len(g.nodes), "lift_sectors": len(g.lift_sectors), "teleport_lines": len(g.teleport_lines)}
    cls = {}
    for it in m["items"]:
        if it["in_deathmatch"]:
            cls[it["class"]] = cls.get(it["class"], 0) + 1
    out["items"] = cls
    out["timed_items"] = sum(1 for it in m["items"] if it["in_deathmatch"] and it["name"] in TIMED)
    for key, (field, thing) in KEY_PICKUPS.items():
        things = [t for t in m[field] if t["in_deathmatch"] and t["name"] == thing]
        reach = []
        for t in things:
            nodes = touch_nodes(g, t["x"], t["y"])
            reach.append({"starts_without_lift": sum(1 for r in no_lift if nodes & r),
                          "starts_with_lift": sum(1 for r in with_lift if nodes & r), "of": len(starts)})
        out[key] = {"count": len(things), "reach": reach}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iwad", action="append", help="IWAD path; repeat with --maps")
    ap.add_argument("--maps", action="append", help="MAP01-MAP10 or E1M1-E1M9, one per --iwad")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    base = os.path.dirname(extract_map.default_iwad())
    pairs = list(zip(a.iwad or [os.path.join(base, "freedoom2.wad"), os.path.join(base, "freedoom1.wad")],
                     a.maps or ["MAP01-MAP10", "E1M1-E1M9"]))
    rows = []
    for iwad, spec in pairs:
        for name in map_names(spec):
            try:
                r = survey(iwad, name)
            except extract_map.WadError as e:
                r = {"map": name, "error": str(e)}
            r["iwad"] = os.path.basename(iwad)
            rows.append(r)
            if "error" in r:
                print(f"{r['iwad']} {name}: {r['error']}")
                continue
            rs = lambda k: [f"{x['starts_without_lift']}/{x['of']} ({x['starts_with_lift']} with lift)" for x in r[k]["reach"]]
            print(f"{r['iwad']} {name}: starts {r['deathmatch_starts']}, weapons {', '.join(r['weapons_present'])}; "
                  f"chainsaw {r['chainsaw']['count']} {rs('chainsaw')}; berserk {r['berserk']['count']} "
                  f"{rs('berserk')}; items {r['items']}, timed {r['timed_items']}; lifts {r['lift_sectors']}, "
                  f"teleport lines {r['teleport_lines']}, nodes {r['nav_nodes']}", flush=True)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
