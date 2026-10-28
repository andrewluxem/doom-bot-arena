"""What the bot knows about enemies: only what its own labels buffer shows.

Labels exist only for objects rendered in the bot's own 90-degree view, so the view cone
and occlusion come from the engine. A live player is labelled `DoomPlayer`; a dead one
`DeadDoomPlayer` (probed on 1.3.2), which is never a target. The bot's own actor can
appear in its own labels; anything within SELF_RADIUS of its position is skipped.

Identity: this is the only module that queries the label identity table (engine/peers.py),
and only for the DoomPlayer labels in the current tic's buffer. A contact is keyed by the
label's object id, stable for an actor's lifetime in this process; its slot is filled in
when the table answers, two tics after the label first appears.

Busy and hurt (PYR's engage_only, characters/README.md) come from the same buffer: a contact is busy
at a tic when it faces another visible player more squarely than it faces this bot (within BUSY_CONE,
inside BUSY_RANGE), as a player sees someone aiming elsewhere; it is hurt when a `Blood` label (the
engine spawns blood where a player is hit) lies within BLOOD_RADIUS of it.

Map items and geometry come from map.json: map knowledge is fair. Objects info is never
used for enemy positions. Item labels (pickups the bot can see) are read by the controller
for bots that time items (characters/README.md `item_timing`).
"""

import math

from engine.peers import NoIdentity

ENEMY = "DoomPlayer"
CORPSE = "DeadDoomPlayer"  # a contact whose last position now shows one has been seen to die
SELF_RADIUS = 20.0
NEW_SIGHTING_GAP = 8  # tics unseen before a reappearance counts as a new sighting
FORGET_TICS = 10 * 35
BLOOD = "Blood"
BLOOD_RADIUS = 48.0
BUSY_CONE = 15.0
BUSY_RANGE = 2048.0


class Contact:
    __slots__ = ("key", "slot", "first_seen", "last_seen", "x", "y", "z", "vx", "vy", "angle", "visible", "burned",
                 "busy_at", "hurt_at")

    def __init__(self, key, slot, t):
        self.key, self.slot = key, slot
        self.first_seen = self.last_seen = t
        self.x = self.y = self.z = self.vx = self.vy = 0.0
        self.angle = None  # facing when last seen (degrees, Doom convention): a player sees where others look
        self.visible = False
        self.burned = False  # an ambusher let this sighting go and will not re-open it
        self.busy_at = self.hurt_at = -10 ** 9  # last tic seen aiming at another player, or bleeding

    def seen_for(self, t):
        return t - self.first_seen

    def position_at(self, t):
        """Last seen position, carried forward by its last seen velocity for up to half a second."""
        dt = min(t - self.last_seen, 17)
        return self.x + self.vx * dt, self.y + self.vy * dt


class Perception:
    def __init__(self, slot):
        self.slot = slot
        self.contacts = {}
        self.last_any_contact = -10 ** 9

    def update(self, t, state, identity, own_x, own_y):
        for c in self.contacts.values():
            c.visible = False
        players, corpses = player_labels(state, own_x, own_y)
        (identity or NoIdentity()).forget([oid for oid, _, _ in corpses])
        names = name_labels(identity, t, players)
        for lab in players:
            oid, x, y = lab.object_id, lab.object_position_x, lab.object_position_y
            c = self.contacts.get(oid)
            if c is None:
                c = self.contacts[oid] = Contact(oid, names[oid], t)
            elif t - c.last_seen > NEW_SIGHTING_GAP:
                c.first_seen, c.burned = t, False
            c.slot = names[oid]  # never keep an old name: ViZDoom can reuse a label id for another actor
            c.last_seen, c.visible = t, True
            c.x, c.y, c.z = x, y, lab.object_position_z
            c.vx, c.vy = lab.object_velocity_x, lab.object_velocity_y
            c.angle = lab.object_angle
            self.last_any_contact = t
        self._busy_and_hurt(t, state, own_x, own_y)
        gone = []
        for k, c in list(self.contacts.items()):
            seen_dying = not c.visible and any(math.hypot(cx - c.x, cy - c.y) < 48 for _, cx, cy in corpses)
            if seen_dying or t - c.last_seen > FORGET_TICS:
                del self.contacts[k]
                gone.append(k)
        (identity or NoIdentity()).forget(gone)

    def _busy_and_hurt(self, t, state, own_x, own_y):
        seen = self.visible()
        blood = [(l.object_position_x, l.object_position_y) for l in state.labels or [] if l.object_name == BLOOD]
        for c in seen:
            if any(math.hypot(bx - c.x, by - c.y) <= BLOOD_RADIUS for bx, by in blood):
                c.hurt_at = t
            if c.angle is None:
                continue
            at_me = abs(_off(c, own_x, own_y))
            for o in seen:
                if o is not c and math.hypot(o.x - c.x, o.y - c.y) <= BUSY_RANGE:
                    off = abs(_off(c, o.x, o.y))
                    if off <= BUSY_CONE and off < at_me:
                        c.busy_at = t
                        break

    def visible(self):
        return [c for c in self.contacts.values() if c.visible]

    def recent(self, t, within):
        return [c for c in self.contacts.values() if t - c.last_seen <= within]

    def latest(self):
        return max(self.contacts.values(), key=lambda c: c.last_seen, default=None)

    def reset(self):
        self.contacts.clear()


def player_labels(state, own_x, own_y):
    """(live player labels, corpses as (object id, x, y)) in this tic's buffer, without the bot's own actor."""
    players, corpses = [], []
    for lab in state.labels or []:
        if lab.object_name == CORPSE:
            corpses.append((lab.object_id, lab.object_position_x, lab.object_position_y))
        elif lab.object_name == ENEMY and math.hypot(lab.object_position_x - own_x,
                                                     lab.object_position_y - own_y) >= SELF_RADIUS:
            players.append(lab)
    return players, corpses


def name_labels(identity, t, labels):
    """{object id: slot or None} for labels in this tic's buffer: the one query of the identity table."""
    return (identity or NoIdentity()).identify(t, [(l.object_id, l.object_position_x, l.object_position_y)
                                                   for l in labels])


def _off(c, x, y):
    """Degrees between contact c's facing and the bearing from c to (x, y)."""
    return (math.degrees(math.atan2(y - c.y, x - c.x)) - c.angle + 180.0) % 360.0 - 180.0


def distance(c, x, y, t):
    cx, cy = c.position_at(t)
    return math.hypot(cx - x, cy - y)
