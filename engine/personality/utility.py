"""Utility scores for the eight chooseable behaviors.

Each behavior gets a base utility in [0, 1] from the situation, multiplied by the sheet's
temperament (characters/README.md: aggression multiplies engage and pursue, self_preservation
multiplies flee and heal) and then by its behavior_weights entry. IN_COMBAT has no weight in
the sheets, so its multiplier is 1. The highest score wins, with a small bonus for the current
behavior so the label does not flicker between near-equal scores.
"""

BEHAVIORS = ("IN_COMBAT", "HUNTING", "ROAMING", "STALKING", "HOLDING_ANGLE", "COLLECTING_ARMOR",
             "COLLECTING_HEALTH", "FLEEING")
HYSTERESIS = 0.05


class Situation:
    """What the decision needs, gathered by the controller each decision tic."""

    def __init__(self, **kw):
        self.health = kw["health"]
        self.armor = kw["armor"]
        self.has_usable = kw["has_usable"]  # holds an allowed weapon with ammo
        self.armed = kw["armed"]  # ...and it is one the sheet rates at least half its best
        self.engageable = kw["engageable"]  # a target the bot may fight right now
        self.threat = kw["threat"]  # an enemy visible or seen in the last second
        self.cornered = kw["cornered"]
        self.recent_contact = kw["recent_contact"]  # an enemy seen in the last 6 s, not now
        self.health_offer = kw["health_offer"]  # 0..1: how much a reachable health item would give
        self.armor_offer = kw["armor_offer"]


def scores(sit, d):
    """d: the controller's Dials. Returns {behavior: weighted score}."""
    w = d.behavior_weights
    hp_need = max(0.0, min(1.0, (100 - sit.health) / 100))
    ar_need = max(0.0, min(1.0, (100 - sit.armor) / 100))
    low_hp_gate = 1.0 if (sit.health >= d.engage_health_min or sit.cornered) else 0.25

    base = {b: 0.0 for b in BEHAVIORS}
    if sit.engageable and sit.has_usable:
        base["IN_COMBAT"] = d.aggression * low_hp_gate
    if sit.has_usable:
        hunt = 0.45 + (0.35 if sit.recent_contact else 0.0)
        if sit.engageable:
            hunt = 0.3
        base["HUNTING"] = d.aggression * hunt * low_hp_gate
    base["ROAMING"] = 0.3 + (0.5 if not sit.armed else 0.0)
    if d.hold_label:
        hold = 0.75 if sit.has_usable else 0.1
        if d.leave_health_below is not None and sit.health < d.leave_health_below:
            hold *= 0.15
        base[d.hold_label] = hold
    base["COLLECTING_HEALTH"] = min(1.0, 2.0 * d.self_preservation * hp_need * d.item_weights.get("health", 0) * sit.health_offer)
    base["COLLECTING_ARMOR"] = min(1.0, 2.0 * (0.3 + 0.7 * d.self_preservation) * ar_need
                                   * d.item_weights.get("armor", 0) * sit.armor_offer)
    if sit.threat:
        flee = 1.5 * hp_need if sit.has_usable else 0.5 + 0.5 * hp_need
        base["FLEEING"] = min(1.0, d.self_preservation * flee)

    mult = {b: (1.0 if b == "IN_COMBAT" else float(w.get(b, 0.0))) for b in BEHAVIORS}
    return {b: round(base[b] * mult[b], 3) for b in BEHAVIORS}


def choose(scored, current):
    best = max(BEHAVIORS, key=lambda b: scored[b])
    if current in scored and scored[current] + HYSTERESIS >= scored[best] and scored[current] > 0:
        return current
    return best
