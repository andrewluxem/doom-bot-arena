"""Shared personality engine (milestone 2A): one controller, driven only by character-sheet dials."""


def prepare(map_data, cache_dir, sheets, traffic=None):
    """Build the nav graph and every spot table the given sheets need, once, before the players start.
    traffic: the map's observed traffic (traffic.build), or None for the route scorer."""
    from engine.personality import nav, spots
    graph = nav.load(map_data, cache_dir)
    tables = {req: spots.table(map_data, cache_dir, [req], graph, traffic) for req in spots.requests_for(sheets)}
    return graph, tables
