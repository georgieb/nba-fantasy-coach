"""Snake-draft pick math and ADP-based survival probabilities."""
from math import erf, sqrt


def team_for_pick(pick: int, order: list[int]) -> int:
    """1-based overall pick -> team id."""
    n = len(order)
    rnd, idx = divmod(pick - 1, n)
    return order[idx] if rnd % 2 == 0 else order[n - 1 - idx]


def picks_for_team(team_id: int, order: list[int], rounds: int) -> list[int]:
    return [k for k in range(1, len(order) * rounds + 1) if team_for_pick(k, order) == team_id]


def next_pick_for_team(team_id: int, order: list[int], rounds: int, after: int) -> int | None:
    """First pick for team strictly after overall pick `after`."""
    return next((k for k in picks_for_team(team_id, order, rounds) if k > after), None)


def _phi(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def _still_there(adp: float, at_pick: float) -> float:
    sd = max(3.0, 0.22 * adp)
    return 1 - _phi((at_pick - adp) / sd)


def survival(adp: float | None, current_pick: int, target_pick: int | None) -> float:
    """P(player available at target_pick | available at current_pick)."""
    if target_pick is None:
        return 0.0
    if target_pick <= current_pick:
        return 1.0
    adp = adp if adp else 400.0
    now = _still_there(adp, current_pick)
    later = _still_there(adp, target_pick)
    if now < 1e-6:  # already well past ADP: treat as a coin flip each round of picks
        return 0.5
    return max(0.0, min(1.0, later / now))
