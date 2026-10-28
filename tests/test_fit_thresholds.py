"""tools/fit_thresholds.py: the threshold search, its rounding and what counts as separating."""

from tools import fit_thresholds as F


def test_fit_maximizes_the_bot_with_the_others_at_most_20_percent_on_each_map():
    own = {"A": [(v,) for v in (0.9, 0.8, 0.7, 0.45, 0.2)], "B": [(v,) for v in (0.85, 0.75, 0.65, 0.6, 0.3)]}
    # five others per map; at most one may pass
    others = {"A": [(v,) for v in (0.1, 0.2, 0.3, 0.4, 0.5)], "B": [(v,) for v in (0.1, 0.1, 0.2, 0.55, 0.62)]}
    e = F.fit([">"], [0.7], own, others)
    assert e["passes"] == 7 and max(e["others_pass_by_map"].values()) <= 0.2
    assert 0.55 <= e["threshold"][0] < 0.6  # the gap that keeps B's 0.6 passing and B's second-best other failing
    assert F.separates(e)


def test_rounding_never_changes_an_outcome():
    vals = [0.789, 0.5, 0.0]
    t = F.roundest(">=", 0.789, vals)
    assert 0.5 < t <= 0.789 and t == 0.6
    assert F.roundest("<", 104.3, [104.3, 98.0, 200.0]) in (100, 99, 101, 102, 103, 104)
    assert F.roundest(">", 1.0, [1.0, 0.0]) == 1.0  # nothing above it


def test_no_feasible_threshold_does_not_separate():
    own = {"A": [(0.1,), (0.1,)]}
    others = {"A": [(0.5,), (0.5,), (0.5,)]}
    assert F.fit([">"], [0.3], own, others)["passes"] == 0
    assert not F.separates(F.fit([">"], [0.3], own, others))


def test_conjunction_is_fitted_jointly():
    # neither part alone keeps the others under 20%, together they do
    own = {"A": [(0.9, 0.9)] * 5}
    others = {"A": [(0.95, 0.1)] * 3 + [(0.1, 0.95)] * 3 + [(0.1, 0.1)] * 4}
    assert F.fit([">"], [0.5], {"A": [(v[0],) for v in own["A"]]}, {"A": [(v[0],) for v in others["A"]]})["passes"] == 0
    e = F.fit([">", ">"], [0.5, 0.5], own, others)
    assert e["passes"] == 5 and e["others_pass_by_map"]["A"] == 0.0 and F.separates(e)
