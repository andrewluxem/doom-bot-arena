"""What the bot knows about enemies: only what its own labels buffer shows.

Labels exist only for objects rendered in the bot's own 90-degree view, so the view cone
and occlusion come from the engine. A live player is labelled `DoomPlayer`; a dead one
`DeadDoomPlayer` (probed on 1.3.2), which is never a target. The bot's own actor can
appear in its own labels; anything within SELF_RADIUS of its position is skipped.

Identity: this is the only module that queries the label identity table (engine/peers.py),
and only for the DoomPlayer labels in the current tic's buffer. A contact is keyed by the
label's object id, stable for an actor's lifetime in this process; its slot is filled in
when the table answers, two tics after the label first appears.

Map items and geometry come from map.json: map knowledge is fair. Objects info is never
used for enemy positions.
"""

import math

from engine.peers import NoIdentity

ENEMY = "DoomPlayer"
CORPSE = "DeadDoomPlayer"  # a contact whose last position now shows one has been seen to die
SELF_RADIUS = 20.0
NEW_SIGHTING_GAP = 8  # tics unseen before a reappearance counts as a new sighting
FORGET_TICS = 10 * 35


class Contact:
    __slots__ = ("key", "slot", "first_seen", "last_seen", "x", "y", "z", "vx", "vy", "visible", "burned")

    def __init__(self, key, slot, t):
        self.key, self.slot = key, slot
        self.first_seen = self.last_seen = t
        self.x = self.y = self.z = self.vx = self.vy = 0.0
        self.visible = False
        self.burned = False  # an ambusher let this sighting go and will not re-open it

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
            self.last_any_contact = t
        gone = []
        for k, c in list(self.contacts.items()):
            seen_dying = not c.visible and any(math.hypot(cx - c.x, cy - c.y) < 48 for _, cx, cy in corpses)
            if seen_dying or t - c.last_seen > FORGET_TICS:
                del self.contacts[k]
                gone.append(k)
        (identity or NoIdentity()).forget(gone)

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


def distance(c, x, y, t):
    cx, cy = c.position_at(t)
    return math.hypot(cx - x, cy - y)
