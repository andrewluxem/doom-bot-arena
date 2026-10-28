"""Load the eight character sheets from characters/*.yaml."""

import glob
import os

import yaml

from engine import obituaries

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHARACTERS = os.path.join(ROOT, "characters")
SLOTS = range(1, 9)

# The only values allowed in tics.jsonl `behavior` (characters/README.md).
BEHAVIORS = ("IN_COMBAT", "HUNTING", "STALKING", "HOLDING_ANGLE", "ROAMING", "COLLECTING_ARMOR",
             "COLLECTING_HEALTH", "FLEEING", "DEAD", "RESPAWNING")


def readme_behaviors(path=os.path.join(CHARACTERS, "README.md")):
    """The vocabulary as written in characters/README.md, to catch drift from BEHAVIORS."""
    with open(path) as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Behavior vocabulary"):
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return tuple(s.strip() for s in nxt.split(","))
    raise ValueError("behavior vocabulary not found in characters/README.md")


def load(path=CHARACTERS):
    sheets = []
    for f in sorted(glob.glob(os.path.join(path, "*.yaml"))):
        with open(f) as fh:
            sheet = yaml.safe_load(fh)
        sheet["_file"] = os.path.relpath(f, ROOT)
        sheets.append(sheet)
    sheets.sort(key=lambda s: s["slot"])
    slots = [s["slot"] for s in sheets]
    if slots != list(SLOTS):
        raise ValueError(f"expected one sheet per slot 1-8, found slots {slots}")
    colorsets = [s["colorset"] for s in sheets]
    if len(set(colorsets)) != len(colorsets):
        raise ValueError(f"colorsets must be unique: {colorsets}")
    obituaries.check_names([s["name"] for s in sheets])
    return sheets


def identity(sheet):
    return {k: sheet[k] for k in ("slot", "code", "name", "archetype", "colorset")}
