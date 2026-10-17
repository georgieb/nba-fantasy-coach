"""Slot eligibility and optimal starting-lineup assignment."""
import numpy as np
from scipy.optimize import linear_sum_assignment

ALL = {"PG", "SG", "SF", "PF", "C"}
SLOT_ELIG = {
    "PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
    "G": {"PG", "SG"}, "F": {"SF", "PF"}, "SG/SF": {"SG", "SF"}, "G/F": {"PG", "SG", "SF", "PF"},
    "PF/C": {"PF", "C"}, "F/C": {"SF", "PF", "C"}, "UTIL": ALL,
}
NON_STARTER = {"BE", "IR"}
NEG = -1e9


def starter_slots(slots: dict[str, int]) -> list[str]:
    """Expand {'PG':1,'UTIL':3,...} into an ordered list, most restrictive slots first."""
    out = [s for s, n in slots.items() if s not in NON_STARTER for _ in range(n)]
    return sorted(out, key=lambda s: len(SLOT_ELIG[s]))


def roster_size(slots: dict[str, int]) -> int:
    return sum(n for s, n in slots.items() if s != "IR")


def eligible(positions, slot: str) -> bool:
    return bool(SLOT_ELIG[slot].intersection(positions))


def best_lineup(players: list[tuple[int, list[str], float]], slots: list[str]):
    """players: [(id, positions, value)]. Returns (total_value, {player_id: slot}) maximizing value."""
    if not players or not slots:
        return 0.0, {}
    m = np.full((len(players), len(slots)), NEG)
    for i, (_, pos, val) in enumerate(players):
        for j, s in enumerate(slots):
            if eligible(pos, s):
                m[i, j] = val
    rows, cols = linear_sum_assignment(m, maximize=True)
    total, assign = 0.0, {}
    for r, c in zip(rows, cols):
        if m[r, c] > NEG / 2:
            total += m[r, c]
            assign[players[r][0]] = slots[c]
    return total, assign


def open_slots(players, slots: list[str]) -> list[str]:
    _, assign = best_lineup(players, slots)
    remaining = list(slots)
    for s in assign.values():
        remaining.remove(s)
    return remaining
