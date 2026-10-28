"""Structured obituaries: the add-on WAD and the only parser for it.

Stock obituaries cannot attribute every death (OB_BARREL names only the victim,
yet the engine credits whoever set the barrel off), and Freedoom rewrites the
multiplayer strings differently per version. The runner therefore loads a
one-lump PWAD whose DEHACKED [STRINGS] section turns every obituary a player
victim can get into

    OBIT|<key>|<victim>|<killer>

ZDoom fills %o with the victim and %k with the credited player; when no player
is credited it sets %k to the victim. PWAD DEHACKED lumps load after the IWAD's,
so these strings win. Nothing about the simulation changes.
"""

import hashlib
import re
import struct
from typing import NamedTuple

PREFIX = "OBIT|"

# Class per key, from ClientObituary (p_interaction.cpp):
#   weapon       a player killed another player; killer != victim, engine +1 to killer
#   telefrag     OB_MPTELEFRAG; killer != victim, engine +1 unless at level start
#   selfkill     attacker == victim; engine -1
#   environment  no player attacker; ZDoom writes the victim into %k; engine unchanged
#   either       OB_DEFAULT: environment when killer == victim, weapon otherwise
#   unsupported  victim field is not the victim, or credit is not visible in the line
KEYS = {
    "OB_SUICIDE": "environment",  # console `kill`; source is NULL, so no frag change
    "OB_FALLING": "environment",
    "OB_CRUSH": "environment",
    "OB_EXIT": "environment",
    "OB_WATER": "environment",
    "OB_SLIME": "environment",
    "OB_LAVA": "environment",
    "OB_MONTELEFRAG": "environment",
    "OB_VOODOO": "unsupported",
    "OB_FRIENDLY1": "unsupported",  # teamplay only; ZDoom swaps the victim for the attacker
    "OB_FRIENDLY2": "unsupported",
    "OB_FRIENDLY3": "unsupported",
    "OB_FRIENDLY4": "unsupported",
    "OB_KILLEDSELF": "selfkill",
    "OB_MPTELEFRAG": "telefrag",
    "OB_DEFAULT": "either",
    "OB_BARREL": "weapon",
    "OB_MPFIST": "weapon",
    "OB_MPCHAINSAW": "weapon",
    "OB_MPPISTOL": "weapon",
    "OB_MPSHOTGUN": "weapon",
    "OB_MPSSHOTGUN": "weapon",
    "OB_MPCHAINGUN": "weapon",
    "OB_MPROCKET": "weapon",
    "OB_MPR_SPLASH": "weapon",
    "OB_MPPLASMARIFLE": "weapon",
    "OB_MPBFG_BOOM": "weapon",
    "OB_MPBFG_SPLASH": "weapon",
    "OB_MPBFG_MBF": "weapon",
    "OB_RAILGUN": "weapon",
    "OB_GRENADE": "weapon",
}
assert len(KEYS) == 31

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")


def dehacked_text():
    lines = ["Patch File for DeHackEd v3.0", "Doom version = 21", "Patch format = 6", "", "[STRINGS]"]
    lines += [f"{key} = {PREFIX}{key}|%o|%k" for key in KEYS]
    return "\n".join(lines) + "\n"


def wad_bytes():
    body = dehacked_text().encode("ascii")
    header = struct.pack("<4sii", b"PWAD", 1, 12 + len(body))
    directory = struct.pack("<ii8s", 12, len(body), b"DEHACKED")
    return header + body + directory


def write_wad(path):
    """Write the add-on WAD and return its sha256."""
    data = wad_bytes()
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


class Obituary(NamedTuple):
    key: str
    victim: str
    killer: str
    raw: str


class Death(NamedTuple):
    type: str  # frag | telefrag | suicide | environmental_death
    actor: str | None  # credited player name, None for environmental deaths
    target: str
    key: str
    raw: str


def parse(line, names):
    """Parse one notification line. Returns (Obituary | None, warning | None).

    Only the OBIT format is accepted; anything else is unknown and never becomes an event.
    """
    if not line.startswith(PREFIX):
        return None, f"unknown notification: {line!r}"
    parts = line.split("|")
    if len(parts) != 4:
        return None, f"malformed obituary (expected 4 fields): {line!r}"
    _, key, victim, killer = parts
    if key not in KEYS:
        return None, f"unknown obituary key {key!r}: {line!r}"
    if victim not in names or killer not in names:
        return None, f"obituary names a player not in this match: {line!r}"
    return Obituary(key, victim, killer, line), None


def classify(ob):
    """Map an obituary to a death event. Returns (Death | None, warning | None)."""
    cls = KEYS[ob.key]
    same = ob.killer == ob.victim
    if cls == "either":
        cls = "environment" if same else "weapon"
    if cls == "weapon" and not same:
        return Death("frag", ob.killer, ob.victim, ob.key, ob.raw), None
    if cls == "telefrag" and not same:
        return Death("telefrag", ob.killer, ob.victim, ob.key, ob.raw), None
    if cls == "selfkill" and same:
        return Death("suicide", ob.victim, ob.victim, ob.key, ob.raw), None
    if cls == "environment" and same:
        return Death("environmental_death", None, ob.victim, ob.key, ob.raw), None
    return None, f"obituary {ob.key} ({cls}) with killer={ob.killer!r} victim={ob.victim!r} is not attributable: {ob.raw!r}"


def check_names(names):
    """Names must survive +name and the '|' format unchanged, and be unique."""
    bad = [n for n in names if not NAME_RE.match(n)]
    if bad:
        raise ValueError(f"player names must match {NAME_RE.pattern}: {bad}")
    if len(set(names)) != len(names):
        raise ValueError(f"player names must be unique: {names}")
