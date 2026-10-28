"""Label identity table fairness (engine/peers.py; docs/MATCH_FORMAT.md, fairness).

The table may be queried only to name labels in the current tic's labels buffer, and only
by engine/personality/perception.py. These tests run the request/answer protocol for three
simulated processes in one process, record every read of the shared array, and fail if
the asking process ever reads anything about a slot absent from its labels buffer.
"""

import ast
import contextlib
import os

from engine import peers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Recorder(list):
    """The shared array, recording which process read which index."""

    def __init__(self, n):
        super().__init__([0.0] * n)
        self.reader = None
        self.reads = []

    def __getitem__(self, i):
        if self.reader is not None:
            self.reads.append((self.reader, i))
        return super().__getitem__(i)


def region(i):
    """('req' or 'ans', parity, asking slot) for a table index."""
    n_req = 2 * peers.SLOTS * peers.MAX_LABELS * peers.REQ_FIELDS
    if i < n_req:
        row = i // (peers.MAX_LABELS * peers.REQ_FIELDS)
        kind = "req"
    else:
        row = (i - n_req) // (peers.MAX_LABELS * peers.ANS_FIELDS)
        kind = "ans"
    return kind, row // peers.SLOTS, row % peers.SLOTS + 1


def table():
    a = Recorder(peers.SIZE)
    for p in (0, 1):
        for s in range(1, peers.SLOTS + 1):
            for i in range(peers.MAX_LABELS):
                list.__setitem__(a, peers._ans(p, s, i) + 1, peers.NO_ANSWER)
    return a


def run(tics, labels_for_asker, positions):
    """Slot 1 asks; slots 2 and 5 answer. positions: slot -> function(t) -> (x, y)."""
    a = table()
    lock = contextlib.nullcontext()
    writers = {s: peers.IdentityWriter(a, lock, s) for s in (1, 2, 5)}
    reader = peers.IdentityReader(a, 1)
    results = []
    for t in range(tics):
        for s, w in writers.items():
            a.reader = f"writer{s}"
            x, y = positions[s](t)
            w.answer(t, x, y, True)
        a.reader = "reader1"
        results.append(reader.identify(t, labels_for_asker(t)))
        a.reader = None
    return a, results


def test_names_a_visible_label_and_never_touches_an_absent_slot():
    # slot 2 walks east in view; slot 5 is far away and never in slot 1's labels buffer
    positions = {1: lambda t: (0.0, 0.0), 2: lambda t: (300.0 + 8 * t, 40.0), 5: lambda t: (1500.0, -900.0)}
    # labels show the previous tic's position, as probed on ViZDoom 1.3.2
    labels = lambda t: [(77, *positions[2](max(t - 1, 0)))]
    a, results = run(6, labels, positions)

    assert results[0] == {77: None} and results[1] == {77: None}  # asked at tic 0, answered at tic 1
    assert all(r == {77: 2} for r in results[2:])

    reader_reads = [region(i) for who, i in a.reads if who == "reader1"]
    assert reader_reads, "the reader read nothing"
    assert {asker for _, _, asker in reader_reads} == {1}, "the reader read another process's requests or answers"
    values = {int(list.__getitem__(a, peers._ans(p, 1, i)))
              for p in (0, 1) for i in range(peers.MAX_LABELS)}
    assert values <= {0, 2}, f"an answer names a slot that is not in the labels buffer: {values}"
    assert not [i for who, i in a.reads if who == "writer5" and region(i)[0] == "ans"], "slot 5 answered"


def test_identify_returns_only_labels_in_this_tics_buffer():
    positions = {1: lambda t: (0.0, 0.0), 2: lambda t: (300.0, 40.0), 5: lambda t: (1500.0, -900.0)}
    labels = lambda t: [(77, 300.0, 40.0)] if t < 3 else []
    _, results = run(6, labels, positions)
    assert results[2] == {77: 2}
    assert results[3:] == [{}, {}, {}], "identify returned a name for a label no longer in the buffer"


def test_no_answer_for_a_label_nobody_matches():
    positions = {1: lambda t: (0.0, 0.0), 2: lambda t: (300.0, 40.0), 5: lambda t: (1500.0, -900.0)}
    labels = lambda t: [(9, 800.0, 800.0)]  # e.g. a player the table has no writer for
    _, results = run(5, labels, positions)
    assert all(r == {9: None} for r in results)


def test_the_table_holds_no_player_positions():
    """Every float written is a label position the asker posted, a slot id, or a distance."""
    positions = {1: lambda t: (0.0, 0.0), 2: lambda t: (300.0, 40.0), 5: lambda t: (1500.0, -900.0)}
    a, _ = run(4, lambda t: [(77, 300.0, 40.0)], positions)
    assert 1500.0 not in a and -900.0 not in a, "slot 5's position reached the shared table"


def _python_files():
    for base in ("engine", "tools", "web"):
        for d, _, files in os.walk(os.path.join(ROOT, base)):
            if "node_modules" in d:
                continue
            for f in files:
                if f.endswith(".py"):
                    yield os.path.relpath(os.path.join(d, f), ROOT)


def test_only_allowed_modules_import_or_query_the_table():
    importers, queriers = set(), set()
    for path in _python_files():
        if path == os.path.join("engine", "peers.py"):
            continue
        tree = ast.parse(open(os.path.join(ROOT, path)).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and (
                    node.module == "engine.peers" or (node.module == "engine" and any(n.name == "peers" for n in node.names))):
                importers.add(path)
            if isinstance(node, ast.Import) and any(n.name == "engine.peers" for n in node.names):
                importers.add(path)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "identify":
                queriers.add(path)
    allowed = {os.path.join("engine", "match.py"), os.path.join("engine", "player.py"),
               os.path.join("engine", "personality", "perception.py")}
    assert importers <= allowed, f"modules importing engine.peers outside the rule: {sorted(importers - allowed)}"
    assert queriers <= {os.path.join("engine", "personality", "perception.py")}, \
        f"identify() called outside perception: {sorted(queriers)}"


def test_a_reused_label_id_is_renamed_or_dropped():
    """ViZDoom can give a new actor an old actor's id: a cached name must not outlive its owner."""
    # slot 2 is seen until tic 40, then dies and goes away; slot 5 then appears under the same label id
    positions = {1: lambda t: (0.0, 0.0), 2: lambda t: (300.0, 40.0) if t < 40 else (3000.0, 3000.0),
                 5: lambda t: (1500.0, -900.0) if t < 40 else (600.0, 80.0)}
    labels = lambda t: [(77, 300.0, 40.0)] if t < 40 else [(77, 600.0, 80.0)]
    _, results = run(120, labels, positions)
    assert results[30] == {77: 2}
    assert results[-1] == {77: 5}, results[-1]
    assert all(r[77] in (None, 5) for r in results[40 + peers.VERIFY_TICS + 3:])
