"""tools/fit_thresholds.py: the threshold search, its rounding and what counts as separating."""

from tools import fit_thresholds as F


def test_fit_maximizes_the_bot_with_the_others_at_most_20_percent_on_each_map():
    own = {"A": [0.9, 0.8, 0.7, 0.45, 0.2], "B": [0.85, 0.75, 0.65, 0.6, 0.3]}
    # five others per map; at most one may pass
    others = {"A": [0.1, 0.2, 0.3, 0.4, 0.5], "B": [0.1, 0.1, 0.2, 0.55, 0.62]}
    e = F.fit(">", 0.7, own, others)
    assert e["passes"] == 7 and max(e["others_pass_by_map"].values()) <= 0.2
    assert 0.55 <= e["threshold"] < 0.6  # the gap that keeps B's 0.6 passing and B's second-best other failing
    assert F.separates(e)


def test_rounding_never_changes_an_outcome():
    vals = [0.789, 0.5, 0.0]
    t = F.roundest(">=", 0.789, vals)
    assert 0.5 < t <= 0.789 and t == 0.6
    assert F.roundest("<", 104.3, [104.3, 98.0, 200.0]) in (100, 99, 101, 102, 103, 104)
    assert F.roundest(">", 1.0, [1.0, 0.0]) == 1.0  # nothing above it


def test_no_feasible_threshold_does_not_separate():
    own = {"A": [0.1, 0.1]}
    others = {"A": [0.5, 0.5, 0.5]}
    assert F.fit(">", 0.3, own, others)["passes"] == 0
    assert not F.separates(F.fit(">", 0.3, own, others))
