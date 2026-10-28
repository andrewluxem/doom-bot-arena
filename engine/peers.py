"""Per-tic table of where each slot is, shared by the eight player processes.

It exists to name players that are already visible. ViZDoom label object ids differ
between processes, so a visible DoomPlayer label is matched to the slot whose
published position is nearest. A human sees the same thing on screen (colour and
name), so this adds identity, not information: no controller may use the table to
locate a player it cannot see.

Double-buffered by tic parity. Each process writes tic t into buffer t % 2 before its
make_action and barrier, and reads tic t - 1 from the other buffer. The barrier at the
end of tic t - 1 guarantees every slot has written it, and no slot can overwrite it
(at tic t + 1) before this process reaches the barrier of tic t.
"""

import math

FIELDS = 4  # x, y, z, alive
SLOTS = 8
MATCH_RADIUS = 48.0  # a player moves at most ~16-30 units per tic; players are 32 apart when touching


def allocate(ctx):
    return ctx.Array("d", [0.0] * (2 * SLOTS * FIELDS), lock=False)


class Peers:
    def __init__(self, array, slot):
        self.a = array
        self.slot = slot

    def write(self, t, x, y, z, alive):
        i = ((t % 2) * SLOTS + self.slot - 1) * FIELDS
        self.a[i:i + FIELDS] = [x, y, z, 1.0 if alive else 0.0]

    def previous(self, t):
        """{slot: (x, y, z, alive)} as published at tic t - 1 (empty at tic 0)."""
        if t == 0:
            return {}
        base = ((t - 1) % 2) * SLOTS * FIELDS
        vals = self.a[base:base + SLOTS * FIELDS]
        return {s + 1: (vals[s * FIELDS], vals[s * FIELDS + 1], vals[s * FIELDS + 2], vals[s * FIELDS + 3] > 0.5)
                for s in range(SLOTS)}

    def identify(self, t, x, y):
        """Slot of the live player nearest (x, y) at tic t - 1, excluding this slot, or None."""
        best = None
        for s, (px, py, _, alive) in self.previous(t).items():
            if s == self.slot or not alive:
                continue
            d = math.hypot(px - x, py - y)
            if d <= MATCH_RADIUS and (best is None or d < best[0]):
                best = (d, s)
        return best[1] if best else None


class NoPeers:
    """Stand-in when a controller runs without the shared table (identity unknown)."""

    def write(self, *a):
        pass

    def previous(self, t):
        return {}

    def identify(self, t, x, y):
        return None
