"""Mock draft: bots pick by ADP with noise and basic roster sense."""
import random

from app.data.espn_client import DEFAULT_SCORING, DEFAULT_SLOTS, League, Player
from app.draft_state import DraftState
from app.engine.roster_fit import best_lineup, roster_size, starter_slots


def new_sim(players: list[Player], teams: int = 10, my_slot: int = 1, seed: int | None = None) -> DraftState:
    team_ids = list(range(1, teams + 1))
    names = {t: ("My Team" if t == my_slot else f"Bot {t}") for t in team_ids}
    league = League(None, 0, "Mock Draft", names, team_ids, dict(DEFAULT_SLOTS), dict(DEFAULT_SCORING),
                    roster_size(DEFAULT_SLOTS))
    st = DraftState(league, players, my_team_id=my_slot, source="sim")
    st.rng = random.Random(seed)
    return st


def bot_pick(st: DraftState) -> int:
    team = st.on_clock
    rng = getattr(st, "rng", random)
    mine = st.rosters()[team]
    slots = starter_slots(st.league.slots)
    entries = [(i, st.players[i].positions, st.fpts[i]) for i in mine]
    base, _ = best_lineup(entries, slots)
    starters_left = len(mine) < len(slots)
    best, best_key = None, float("inf")
    for i in sorted(st.available(), key=lambda i: st.players[i].adp or st.players[i].rank or 999)[:40]:
        p = st.players[i]
        adp = p.adp or p.rank or 300
        key = adp + rng.gauss(0, 2 + 0.08 * adp)
        if starters_left:
            gain, _ = best_lineup(entries + [(i, p.positions, st.fpts[i])], slots)
            if gain - base < 0.5 * st.fpts[i]:   # wouldn't start for them -> less likely
                key += 15
        if key < best_key:
            best, best_key = i, key
    return best


def advance_to_me(st: DraftState) -> int:
    """Bots pick until it's our turn (or the draft ends). Returns picks made."""
    n = 0
    while not st.complete and st.on_clock != st.my_team_id:
        st.add_pick(bot_pick(st))
        n += 1
    return n
