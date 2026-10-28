"""Label identity across the eight player processes.

ViZDoom label object ids differ between processes, so a process cannot tell which slot a
visible `DoomPlayer` label belongs to. It asks. For each label it cannot name yet, it posts
the label's position as a request. Every other process compares open requests with its own
position and answers "that is me" when one matches. The shared table holds only requests
(positions of labels the asking process can see) and answers (slot ids). No process
publishes its own position, so nothing in the table can locate a player that is not on
the asker's screen, and the asker learns nothing but the slot of labels it posted.

Fairness rule (docs/MATCH_FORMAT.md):
- Only engine/personality/perception.py may query the table (`IdentityReader.identify`).
- It may query it only for labels present in the current tic's labels buffer.
- engine/player.py creates a process's reader and writer and runs the writer's `answer`.
  engine/match.py allocates the table. No other module imports this one.
tests/test_peers.py enforces both.

Timing, with the barrier at the end of every tic ordering the steps:
- A request posted at tic t is answered during tic t + 1: each process answers right after
  its get_state, before its controller acts.
- The asker reads the answer at tic t + 2.
- Answers are cached per label object id while the label stays in view. ViZDoom can give a
  new actor an id an old one had: 2A evaluation matches named players who had been dead for
  over a second. So a name is used only while its label has been seen every tic without a
  jump; a label that reappears after a gap, or moves more than JUMP units in a tic, waits for
  a fresh answer like a new one. Cached names are also asked again every VERIFY_TICS and
  dropped if nobody answers, and a corpse's id is dropped as soon as it is seen.

Labels report an actor's position at its own tic or the one before, sampled slightly apart
from the game variables (probed on 1.3.2: 81% equal the previous tic's position exactly, the
rest within a tic's movement). An answer therefore compares the request with the answerer's
own position at both tics, and accepts it within MATCH_RADIUS. Live players are at least 32
units apart. If two answerers both match, the nearer one wins, under a lock.
"""

import math

SLOTS = 8
MAX_LABELS = 16  # requests per process per tic
REQ_FIELDS = 3  # x, y, used
ANS_FIELDS = 2  # slot, distance
MATCH_RADIUS = 20.0
NO_ANSWER = 1e9
VERIFY_TICS = 4  # a reused id keeps a wrong name at most this plus the two-tic answer delay
JUMP = 64.0  # more than a player can move in a tic


def _req(parity, asker, i):
    return ((parity * SLOTS + asker - 1) * MAX_LABELS + i) * REQ_FIELDS


def _ans(parity, asker, i):
    return 2 * SLOTS * MAX_LABELS * REQ_FIELDS + ((parity * SLOTS + asker - 1) * MAX_LABELS + i) * ANS_FIELDS


SIZE = 2 * SLOTS * MAX_LABELS * (REQ_FIELDS + ANS_FIELDS)


def allocate(ctx):
    """The shared table and the lock answerers take. Answer distances start at NO_ANSWER."""
    array = ctx.Array("d", [0.0] * SIZE, lock=False)
    for p in (0, 1):
        for a in range(1, SLOTS + 1):
            for i in range(MAX_LABELS):
                array[_ans(p, a, i) + 1] = NO_ANSWER
    return array, ctx.Lock()


class IdentityWriter:
    """One process's answering side. It keeps its own recent positions in local memory only."""

    def __init__(self, array, lock, slot):
        self.a, self.lock, self.slot = array, lock, slot
        self.history = {}  # tic -> (x, y) while alive

    def answer(self, t, x, y, alive):
        """Record this tic's own position, then answer requests posted at t - 1 that match it."""
        if alive:
            self.history[t] = (x, y)
        self.history.pop(t - 3, None)
        if t < 1:
            return
        mine = [self.history[k] for k in (t - 1, t - 2) if k in self.history]
        if not mine:
            return
        p = (t - 1) % 2
        for asker in range(1, SLOTS + 1):
            if asker == self.slot:
                continue
            for i in range(MAX_LABELS):
                base = _req(p, asker, i)
                if self.a[base + 2] < 0.5:
                    break  # requests are packed from index 0
                rx, ry = self.a[base], self.a[base + 1]
                d = min(math.hypot(rx - mx, ry - my) for mx, my in mine)
                if d <= MATCH_RADIUS:
                    with self.lock:
                        ab = _ans(p, asker, i)
                        if d < self.a[ab + 1]:
                            self.a[ab], self.a[ab + 1] = float(self.slot), d


class IdentityReader:
    """One process's asking side. Use it only through engine/personality/perception.py."""

    def __init__(self, array, slot):
        self.a, self.slot = array, slot
        self.known = {}  # label object id -> slot
        self.checked = {}  # label object id -> tic its name was last asked
        self.seen = {}  # label object id -> (tic, x, y) it was last in this process's labels
        self.posted = {}  # tic -> [object id per request index]

    def identify(self, t, labels):
        """{object id: slot or None} for `labels`, the DoomPlayer labels in this tic's buffer.

        `labels` is [(object_id, x, y)]. Only these labels are posted or looked up.
        """
        p = t % 2
        asked = self.posted.pop(t - 2, [])
        for i, oid in enumerate(asked):
            ab = _ans(p, self.slot, i)
            if self.a[ab + 1] < NO_ANSWER:
                self.known[oid] = int(self.a[ab])
            else:
                self.known.pop(oid, None)  # nobody claims it (any more)
            self.a[ab], self.a[ab + 1] = 0.0, NO_ANSWER
        for i in range(MAX_LABELS):
            self.a[_req(p, self.slot, i) + 2] = 0.0
        out, post = {}, []
        for oid, x, y in labels:
            last = self.seen.get(oid)
            if last is None or t - last[0] > 1 or math.hypot(x - last[1], y - last[2]) > JUMP:
                self.known.pop(oid, None)  # out of view or jumped: possibly another actor now
            self.seen[oid] = (t, x, y)
            out[oid] = self.known.get(oid)
            stale = t - self.checked.get(oid, -VERIFY_TICS) >= VERIFY_TICS
            if (out[oid] is None or stale) and len(post) < MAX_LABELS:
                self.checked[oid] = t
                base = _req(p, self.slot, len(post))
                self.a[base], self.a[base + 1], self.a[base + 2] = x, y, 1.0
                post.append(oid)
        self.posted[t] = post
        return out

    def forget(self, oids):
        """Drop cached names for actors this process no longer sees (dead and gone)."""
        for oid in oids:
            self.known.pop(oid, None)
            self.checked.pop(oid, None)
            self.seen.pop(oid, None)


class NoIdentity:
    """Stand-in when a controller runs without the shared table: no label can be named."""

    def identify(self, t, labels):
        return {oid: None for oid, _, _ in labels}

    def forget(self, oids):
        pass
