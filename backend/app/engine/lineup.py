"""Daily lineup optimizer (recommend-only: the user makes the moves in ESPN).

Lineups lock per player at his game's tip-off, so each day is optimized independently:
start the best eligible players who have a game, bench the rest."""
from dataclasses import dataclass

from app.data.espn_client import Game, Player, RosterSpot
from app.engine.roster_fit import best_lineup, starter_slots

OUT = {"OUT", "SUSPENSION", "INJURY_RESERVE"}
QUESTIONABLE = {"DAY_TO_DAY", "QUESTIONABLE", "DOUBTFUL", "GAME_TIME_DECISION"}
RECENT_WEIGHT = 0.3   # blend of last-15-day form into the season projection
RECENT_MIN_GP = 3


def per_game(p: Player, scoring: dict[int, float]) -> float:
    """Expected fantasy points per game: projection, nudged by recent form once there's enough of it."""
    proj = sum(p.proj_raw.get(s, 0.0) * w for s, w in scoring.items()) / p.gp if p.gp else 0.0
    if p.recent and p.recent_gp >= RECENT_MIN_GP:
        recent = sum(p.recent.get(s, 0.0) * w for s, w in scoring.items())
        return round((1 - RECENT_WEIGHT) * proj + RECENT_WEIGHT * recent, 1)
    return round(proj, 1)


@dataclass
class DayPlan:
    sp: int
    total: float                       # expected points from the optimal starters
    assign: dict[int, str]             # player id -> optimal slot (starters only)
    games: dict[int, Game]             # player id -> today's game
    available: set[int]                # players with a game who aren't OUT / IR


def plan_day(roster: list[RosterSpot], sp: int, schedule: dict[str, dict[int, Game]], scoring: dict[int, float],
             slots: dict[str, int], now: float = 0.0) -> DayPlan:
    """Optimal starters for one day. Players whose game has tipped off are locked where they are."""
    games = {s.player.id: schedule.get(s.player.team, {}).get(sp) for s in roster}
    games = {pid: g for pid, g in games.items() if g}
    available = {s.player.id for s in roster
                 if s.player.id in games and s.slot != "IR" and s.player.injury not in OUT}
    open_slots, assign, total = starter_slots(slots), {}, 0.0
    for s in roster:  # locked starters keep their slot (and count); locked bench players can't come in
        g = games.get(s.player.id)
        if g and g.start <= now:
            available.discard(s.player.id)
            if s.slot in open_slots:
                open_slots.remove(s.slot)
                assign[s.player.id] = s.slot
                total += per_game(s.player, scoring)
    entries = [(s.player.id, s.player.positions, per_game(s.player, scoring)) for s in roster if s.player.id in available]
    rest, rest_assign = best_lineup(entries, open_slots)
    return DayPlan(sp, round(float(total + rest), 1), {**assign, **rest_assign}, games, available)


def daily_moves(roster: list[RosterSpot], plan: DayPlan, scoring: dict[int, float], now: float) -> dict:
    """Compare ESPN's current lineup with the optimal one and list what to change before each tip-off."""
    rows, moves, flags = [], [], []
    for s in roster:
        p, g = s.player, plan.games.get(s.player.id)
        target = plan.assign.get(p.id, "IR" if s.slot == "IR" else "BE")
        locked = bool(g and g.start <= now)
        currently_starting = s.slot not in ("BE", "IR")
        should_start = p.id in plan.assign
        row = {"player_id": p.id, "name": p.name, "team": p.team, "positions": p.positions, "injury": p.injury,
               "fpg": per_game(p, scoring), "current": s.slot, "target": target, "locked": locked,
               "game": {"opp": g.opp, "home": g.home, "start": g.start} if g else None}
        rows.append(row)
        if locked:
            continue
        if should_start and not currently_starting:
            moves.append({"action": "start", "player_id": p.id, "name": p.name, "slot": target,
                          "why": f"{per_game(p, scoring)} FP/G, {'vs' if g.home else '@'} {g.opp}"})
        elif currently_starting and not should_start:
            why = "no game today" if not g else ("out" if p.injury in OUT else "better options today")
            moves.append({"action": "bench", "player_id": p.id, "name": p.name, "slot": "BE", "why": why})
        if should_start and p.injury in QUESTIONABLE:
            flags.append(f"{p.name} is {p.injury.replace('_', ' ').lower()}: check status before tip-off")
    rows.sort(key=lambda r: (r["target"] in ("BE", "IR"), -r["fpg"]))
    return {"sp": plan.sp, "expected_points": plan.total, "lineup": rows, "moves": moves, "flags": flags,
            "games_today": len(plan.games), "starters_with_games": len(plan.assign)}
