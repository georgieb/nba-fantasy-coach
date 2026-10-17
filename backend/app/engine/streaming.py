"""Free-agent streaming suggestions (recommend-only: the user makes any add/drop in ESPN).

A stream is worth it when swapping a low-value rostered player for a free agent raises the
team's optimal-lineup points over the rest of the matchup week."""
from app.data.espn_client import Player, RosterSpot
from app.engine.lineup import OUT, per_game
from app.engine.matchup import project_week

DROP_CANDIDATES = 3    # only consider dropping the lowest-value players on the roster
FA_POOL = 40
MIN_GAIN = 15.0        # expected fantasy points over the rest of the week
MAX_DROP_RATIO = 1.05  # never suggest dropping a player clearly better per game than the pickup


def suggest_streams(roster: list[RosterSpot], free_agents: list[Player], days: list[int], schedule, scoring, slots,
                    now: float, top: int = 5) -> list[dict]:
    if not days or not roster:
        return []
    base = project_week(roster, days, schedule, scoring, slots, now)["points"]
    droppable = sorted((s for s in roster if s.slot != "IR"), key=lambda s: per_game(s.player, scoring))[:DROP_CANDIDATES]
    mine = {s.player.id for s in roster}
    pool = sorted((p for p in free_agents if p.injury not in OUT and p.id not in mine),
                  key=lambda p: -per_game(p, scoring))[:FA_POOL]
    out = []
    for fa in pool:
        games_left = sum(1 for sp in days if sp in schedule.get(fa.team, {}))
        if not games_left:
            continue
        best = None
        for d in droppable:
            if per_game(d.player, scoring) > per_game(fa, scoring) * MAX_DROP_RATIO:
                continue
            trial = [s for s in roster if s.player.id != d.player.id] + [RosterSpot(fa, "BE")]
            gain = project_week(trial, days, schedule, scoring, slots, now)["points"] - base
            if best is None or gain > best[0]:
                best = (gain, d)
        if best and best[0] >= MIN_GAIN:
            gain, d = best
            out.append({"add": {"player_id": fa.id, "name": fa.name, "team": fa.team, "positions": fa.positions,
                                "fpg": per_game(fa, scoring), "games_left": games_left},
                        "drop": {"player_id": d.player.id, "name": d.player.name, "fpg": per_game(d.player, scoring),
                                 "games_left": sum(1 for sp in days if sp in schedule.get(d.player.team, {}))},
                        "gain": round(float(gain), 1),
                        "season_note": "about even per game - this is a schedule/volume play"
                        if per_game(d.player, scoring) > per_game(fa, scoring) else "upgrade on per-game value too"})
    out.sort(key=lambda r: -r["gain"])
    return out[:top]
