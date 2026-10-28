"""Rocket fire safety (engine/personality/ballistics.py): a bot in a corner must not fire into it."""

import pytest

from engine.personality.ballistics import FLIGHT_CHECK, RocketCheck


def room(extra_lines=(), extra_sectors=(), obstacles=()):
    """A 512x512 room, floor 0, ceiling 128; one-sided walls with the room on their right."""
    corners = [(0, 0), (0, 512), (512, 512), (512, 0)]  # clockwise: interior on the right
    lines = [{"id": i, "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "flags": 1, "two_sided": False,
              "impassable": True, "special": 0, "tag": 0, "front_sector": 0, "back_sector": None}
             for i, (a, b) in enumerate(zip(corners, corners[1:] + corners[:1]))]
    sectors = [{"id": 0, "floor": 0, "ceiling": 128, "light": 160, "special": 0, "tag": 0}]
    return {"bounds": {"min_x": 0, "min_y": 0, "max_x": 512, "max_y": 512},
            "linedefs": lines + list(extra_lines), "sectors": sectors + list(extra_sectors),
            "obstacles": list(obstacles)}


@pytest.fixture
def corner():
    return RocketCheck(room())


@pytest.mark.parametrize("yaw", [225.0, 180.0, 270.0, 200.0])
def test_bot_in_corner_refuses_to_fire_into_it(corner, yaw):
    ok, d = corner.clear(40, 40, 0, yaw)
    assert not ok and d < FLIGHT_CHECK


@pytest.mark.parametrize("yaw", [45.0, 30.0, 60.0])
def test_bot_in_corner_fires_out_of_it(corner, yaw):
    assert corner.clear(40, 40, 0, yaw) == (True, None)


def test_rocket_radius_counts_alongside_a_wall(corner):
    # 40 units from the west wall, flying north: the rocket's side passes 29 units from it
    assert corner.clear(40, 40, 0, 90.0)[0]
    # 8 units from it the rocket's side would scrape the wall
    assert not corner.clear(8, 40, 0, 90.0)[0]


def test_aiming_down_hits_the_floor_close_by(corner):
    ok, d = corner.clear(256, 256, 0, 0.0, pitch_up_deg=-30.0)
    assert not ok and d == pytest.approx(32 / 0.5774, rel=0.01)
    assert corner.clear(256, 256, 0, 0.0, pitch_up_deg=-5.0)[0]  # lands far beyond 160


def test_a_step_up_blocks_but_a_low_one_does_not():
    # a platform from x = 300 to 400 across the room; the rocket flies east from x = 200
    def step(height):
        line = {"id": 10, "x1": 300, "y1": 512, "x2": 300, "y2": 0, "flags": 4, "two_sided": True,
                "impassable": False, "special": 0, "tag": 0, "front_sector": 0, "back_sector": 1}
        far = {"id": 11, "x1": 400, "y1": 0, "x2": 400, "y2": 512, "flags": 4, "two_sided": True,
               "impassable": False, "special": 0, "tag": 0, "front_sector": 0, "back_sector": 1}
        plat = {"id": 1, "floor": height, "ceiling": 128, "light": 160, "special": 0, "tag": 0}
        return RocketCheck(room([line, far], [plat]))
    assert not step(64).clear(200, 256, 0, 0.0)[0]
    assert step(16).clear(200, 256, 0, 0.0)[0]


def test_a_solid_thing_in_the_way_blocks(corner):
    blocked = RocketCheck(room(obstacles=[{"x": 300, "y": 256, "radius": 16, "in_deathmatch": True}]))
    assert not blocked.clear(200, 256, 0, 0.0)[0]
    assert corner.clear(200, 256, 0, 0.0)[0]


def test_doom_box_splash_reaches_further_on_a_diagonal(corner):
    # a wall hit 180 units away at 45 degrees is only 127 units away in Doom's box distance
    ok, _ = corner.clear(130, 130, 0, 225.0)
    assert not ok
    assert corner.clear(300, 300, 0, 225.0)[0]


def test_players_in_the_line_of_fire(corner):
    assert not corner.clear(100, 256, 0, 0.0, players=[(220, 256, 0, 0)])[0]  # 120 ahead
    assert corner.clear(100, 256, 0, 0.0, players=[(400, 256, 0, 0)])[0]  # 300 ahead
    # a target 250 away charging at 16 units per tic meets the rocket inside the splash reach
    assert not corner.clear(100, 256, 0, 0.0, players=[(350, 256, -16, 0)])[0]
    # a bystander off the line of fire does not matter
    assert corner.clear(100, 256, 0, 0.0, players=[(160, 400, 0, 0), (420, 256, 0, 0)])[0]
