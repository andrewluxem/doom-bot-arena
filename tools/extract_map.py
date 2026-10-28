"""Extract one level's geometry, deathmatch starts and weapons from a WAD.

Reads the Doom-format map lumps directly (no engine needed) and writes map.json
for that level only. Hexen-format and UDMF maps are rejected, not guessed at.

    python tools/extract_map.py --iwad path/to/freedoom2.wad --map MAP01
"""

import argparse
import hashlib
import json
import os
import struct
import sys

MAP_FORMAT_VERSION = 1

# Doom II / Freedoom Phase 2 weapon things. The pistol has no thing; every player spawns with it.
WEAPON_THINGS = {
    2005: "chainsaw",
    2001: "shotgun",
    82: "super_shotgun",
    2002: "chaingun",
    2003: "rocket_launcher",
    2004: "plasma_rifle",
    2006: "bfg",
}
DEATHMATCH_START = 11
PLAYER_STARTS = {1: 1, 2: 2, 3: 3, 4: 4}
EXPLOSIVE_BARREL = 2035

# Doom-format thing flags.
FLAG_EASY, FLAG_MEDIUM, FLAG_HARD = 0x1, 0x2, 0x4
FLAG_AMBUSH = 0x8
FLAG_MULTIPLAYER_ONLY = 0x10
FLAG_NOT_DEATHMATCH = 0x20  # Boom
FLAG_NOT_COOP = 0x40  # Boom
FLAG_BOOM_RESERVED = 0x100  # set by broken editors; ZDoom then ignores 0x20/0x40/0x80

# ViZDoom skill (1-5) to the thing flag that must be set for the thing to spawn.
SKILL_FLAG = {1: FLAG_EASY, 2: FLAG_EASY, 3: FLAG_MEDIUM, 4: FLAG_HARD, 5: FLAG_HARD}

LINEDEF_FLAGS = {
    "impassable": 0x1,
    "blocks_monsters": 0x2,
    "two_sided": 0x4,
    "secret": 0x20,
    "blocks_sound": 0x40,
    "never_on_automap": 0x80,
}

MAP_LUMPS = ("THINGS", "LINEDEFS", "SIDEDEFS", "VERTEXES", "SEGS", "SSECTORS",
             "NODES", "SECTORS", "REJECT", "BLOCKMAP", "BEHAVIOR")


class WadError(Exception):
    pass


def read_directory(data):
    if len(data) < 12:
        raise WadError("file too small to be a WAD")
    ident, count, offset = struct.unpack_from("<4sii", data, 0)
    if ident not in (b"IWAD", b"PWAD"):
        raise WadError(f"not a WAD (header {ident!r})")
    lumps = []
    for i in range(count):
        pos, size, name = struct.unpack_from("<ii8s", data, offset + 16 * i)
        lumps.append((name.rstrip(b"\0").decode("ascii", "replace").upper(), pos, size))
    return ident.decode(), lumps


def map_lumps(data, lumps, map_name):
    map_name = map_name.upper()
    for i, (name, _, _) in enumerate(lumps):
        if name == map_name:
            found = {}
            for name2, pos, size in lumps[i + 1:]:
                if name2 not in MAP_LUMPS or name2 in found:
                    break
                found[name2] = data[pos:pos + size]
            if "TEXTMAP" in found or (i + 1 < len(lumps) and lumps[i + 1][0] == "TEXTMAP"):
                raise WadError(f"{map_name} is UDMF; only Doom-format maps are supported")
            if "BEHAVIOR" in found:
                raise WadError(f"{map_name} is Hexen-format; only Doom-format maps are supported")
            for required in ("THINGS", "LINEDEFS", "SIDEDEFS", "VERTEXES", "SECTORS"):
                if required not in found:
                    raise WadError(f"{map_name} is missing its {required} lump")
            return found
    raise WadError(f"map {map_name} not found")


def unpack_all(fmt, blob):
    size = struct.calcsize(fmt)
    if len(blob) % size:
        raise WadError(f"lump size {len(blob)} is not a multiple of {size}")
    return [struct.unpack_from(fmt, blob, off) for off in range(0, len(blob), size)]


def spawns_in_deathmatch(flags, skill):
    if not flags & SKILL_FLAG[skill]:
        return False
    if not flags & FLAG_BOOM_RESERVED and flags & FLAG_NOT_DEATHMATCH:
        return False
    return True


def extract(iwad_path, map_name, skill=3):
    with open(iwad_path, "rb") as f:
        data = f.read()
    wad_type, lumps = read_directory(data)
    found = map_lumps(data, lumps, map_name)

    vertexes = unpack_all("<hh", found["VERTEXES"])
    sidedefs = unpack_all("<hh8s8s8sH", found["SIDEDEFS"])
    sectors_raw = unpack_all("<hh8s8shhh", found["SECTORS"])
    linedefs_raw = unpack_all("<HHHHHHH", found["LINEDEFS"])
    things_raw = unpack_all("<hhhhH", found["THINGS"])

    def side_sector(side):
        if side == 0xFFFF:
            return None
        if side >= len(sidedefs):
            raise WadError(f"linedef references missing sidedef {side}")
        return sidedefs[side][5]

    linedefs = []
    for idx, (v1, v2, flags, special, tag, front, back) in enumerate(linedefs_raw):
        x1, y1 = vertexes[v1]
        x2, y2 = vertexes[v2]
        linedefs.append({
            "id": idx,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "flags": flags,
            "two_sided": back != 0xFFFF,
            "impassable": bool(flags & LINEDEF_FLAGS["impassable"]),
            "special": special,
            "tag": tag,
            "front_sector": side_sector(front),
            "back_sector": side_sector(back),
        })

    sectors = [{"id": i, "floor": fl, "ceiling": ce, "light": light, "special": sp, "tag": tag}
               for i, (fl, ce, _, _, light, sp, tag) in enumerate(sectors_raw)]

    dm_starts, player_starts, weapons, barrels = [], [], [], []
    for idx, (x, y, angle, ttype, flags) in enumerate(things_raw):
        if ttype == DEATHMATCH_START:
            dm_starts.append({"thing": idx, "x": x, "y": y, "angle": angle})
        elif ttype in PLAYER_STARTS:
            player_starts.append({"thing": idx, "player": PLAYER_STARTS[ttype], "x": x, "y": y, "angle": angle})
        elif ttype in WEAPON_THINGS:
            weapons.append({
                "thing": idx,
                "type": ttype,
                "name": WEAPON_THINGS[ttype],
                "x": x, "y": y, "angle": angle,
                "flags": flags,
                "multiplayer_only": bool(flags & FLAG_MULTIPLAYER_ONLY),
                "in_deathmatch": spawns_in_deathmatch(flags, skill),
            })
        elif ttype == EXPLOSIVE_BARREL:
            barrels.append({"thing": idx, "x": x, "y": y, "in_deathmatch": spawns_in_deathmatch(flags, skill)})

    xs = [v[0] for v in vertexes]
    ys = [v[1] for v in vertexes]
    return {
        "map_format_version": MAP_FORMAT_VERSION,
        "source": {
            "iwad": os.path.basename(iwad_path),
            "iwad_sha256": hashlib.sha256(data).hexdigest(),
            "wad_type": wad_type,
            "map": map_name.upper(),
            "format": "doom",
            "skill": skill,
        },
        "bounds": {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)},
        "linedefs": linedefs,
        "sectors": sectors,
        "deathmatch_starts": dm_starts,
        "player_starts": player_starts,
        "weapons": weapons,
        "weapons_present": sorted({w["name"] for w in weapons if w["in_deathmatch"]}),
        "barrels": barrels,
    }


def write_map_json(map_data, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(map_data, f, separators=(",", ":"))
        f.write("\n")


def default_iwad():
    import vizdoom
    return os.path.join(os.path.dirname(vizdoom.__file__), "freedoom2.wad")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--iwad", help="IWAD path (default: freedoom2.wad shipped with ViZDoom)")
    p.add_argument("--map", default="MAP01")
    p.add_argument("--skill", type=int, default=3, choices=range(1, 6),
                   help="ViZDoom skill used to decide which things spawn (default 3)")
    p.add_argument("--out", default=os.path.join("tools", "out", "map.json"))
    args = p.parse_args(argv)

    iwad = args.iwad or default_iwad()
    try:
        m = extract(iwad, args.map, args.skill)
    except (OSError, WadError) as e:
        print(f"extract_map: {e}", file=sys.stderr)
        return 1
    write_map_json(m, args.out)

    print(f"{m['source']['iwad']} {m['source']['map']} sha256={m['source']['iwad_sha256'][:12]}")
    print(f"linedefs: {len(m['linedefs'])}  sectors: {len(m['sectors'])}")
    print(f"deathmatch starts: {len(m['deathmatch_starts'])}")
    print("weapons:")
    for w in m["weapons"]:
        print(f"  {w['name']:<16} thing {w['thing']:>4} at ({w['x']}, {w['y']})"
              f"  flags=0x{w['flags']:03x} mp_only={w['multiplayer_only']} in_dm={w['in_deathmatch']}")
    print(f"weapons present in deathmatch: {', '.join(m['weapons_present']) or 'none'}")
    print(f"explosive barrels: {len(m['barrels'])} ({sum(b['in_deathmatch'] for b in m['barrels'])} in deathmatch)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
