"""engine.geometry.LineOfSight: Doom-style sight windows through two-sided lines."""

from engine.geometry import LineOfSight


def corridor(mid_floor=0, mid_ceil=128, wall=False):
    """Three sectors along x: 0..100, 100..200 (the middle), 200..300, all 0..128 except the middle."""
    sectors = [{"floor": 0, "ceiling": 128}, {"floor": mid_floor, "ceiling": mid_ceil}, {"floor": 0, "ceiling": 128}]
    lines = [{"x1": 100, "y1": -50, "x2": 100, "y2": 50, "front_sector": 0, "back_sector": 1, "two_sided": True},
             {"x1": 200, "y1": -50, "x2": 200, "y2": 50, "front_sector": 1, "back_sector": 2, "two_sided": True}]
    if wall:
        lines.append({"x1": 150, "y1": -50, "x2": 150, "y2": 50, "front_sector": 1, "back_sector": None,
                      "two_sided": False})
    return LineOfSight({"sectors": sectors, "linedefs": lines,
                        "bounds": {"min_x": -10, "min_y": -60, "max_x": 310, "max_y": 60}})


def test_open_corridor_and_walls():
    assert corridor().clear(50, 0, 41, 250, 0, 0, 56)
    assert not corridor(wall=True).clear(50, 0, 41, 250, 0, 0, 56)
    assert not corridor(mid_ceil=0).clear(50, 0, 41, 250, 0, 0, 56)  # a closed door


def test_window_narrows_with_heights():
    # a 40-unit step in the middle hides a target's body but not its head over 250 units
    assert corridor(mid_floor=40).clear(50, 0, 41, 250, 0, 0, 56)
    assert not corridor(mid_floor=60).clear(50, 0, 41, 250, 0, 0, 56)
    # a low ceiling below eye height: the line to the target's feet passes x = 100 at height 30.75
    assert corridor(mid_ceil=35).clear(50, 0, 41, 250, 0, 0, 56)
    assert not corridor(mid_ceil=30).clear(50, 0, 41, 250, 0, 0, 56)
    # live heights override the map: the door opens
    assert corridor(mid_ceil=0).clear(50, 0, 41, 250, 0, 0, 56, live={1: (0, 128)})


def test_near_shared_line():
    from engine.geometry import MapGeometry
    lines = [{"x1": 100, "y1": -50, "x2": 100, "y2": 50, "front_sector": 0, "back_sector": 1},
             {"x1": 200, "y1": -50, "x2": 200, "y2": 50, "front_sector": 1, "back_sector": 2}]
    geo = MapGeometry({"bounds": {"min_x": 0, "min_y": -60, "max_x": 300, "max_y": 60}, "sectors": [{}, {}, {}],
                       "linedefs": lines})
    assert geo.near_shared_line(110, 0, 0, 1, 16) and geo.near_shared_line(110, 0, 1, 0, 16)
    assert not geo.near_shared_line(120, 0, 0, 1, 16)
    assert not geo.near_shared_line(110, 0, 0, 2, 16)  # 0 and 2 share no line
