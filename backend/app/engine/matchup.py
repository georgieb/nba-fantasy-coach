"""Matchup weeks and weekly projections.

ESPN only exposes which days belong to a matchup week after games are played. For future weeks we use
the shape ESPN uses for this league: weeks 2..N are Monday-Sunday blocks ending on the final scoring
period, and week 1 absorbs the extra opening days (13 + 22 x 7 = 167 for 2026-27)."""
import datetime as dt

from app.data.espn_client import Game, RosterSpot
from app.engine.lineup import plan_day


def season_start(schedule: dict[str, dict[int, Game]]) -> dt.date:
    g = next(g for games in schedule.values() for g in games.values())
    return g.date - dt.timedelta(days=g.sp - 1)


def scoring_period_for(day: dt.date, start: dt.date) -> int:
    return (day - start).days + 1


def matchup_days(final_sp: int, matchup_count: int, observed: dict[int, list[int]] | None = None) -> dict[int, list[int]]:
    """Matchup period -> scoring periods (days). Observed ranges from ESPN override the inferred ones."""
    weeks, end = {}, final_sp
    for mp in range(matchup_count, 1, -1):
        weeks[mp] = list(range(end - 6, end + 1))
        end -= 7
    weeks[1] = list(range(1, end + 1))
    for mp, sps in (observed or {}).items():
        weeks[mp] = sorted(set(weeks.get(mp, [])) | set(sps))
    return dict(sorted(weeks.items()))


def observed_days(schedule_items: list[dict]) -> dict[int, list[int]]:
    out: dict[int, set[int]] = {}
    for m in schedule_items:
        for side in ("home", "away"):
            sps = (m.get(side) or {}).get("pointsByScoringPeriod") or {}
            out.setdefault(m.get("matchupPeriodId"), set()).update(int(k) for k in sps)
    return {k: sorted(v) for k, v in out.items() if v}


def project_week(roster: list[RosterSpot], days: list[int], schedule, scoring, slots, now: float) -> dict:
    """Expected points over the given days with an optimal lineup each day, plus starts (games played)."""
    per_day = []
    for sp in days:
        plan = plan_day(roster, sp, schedule, scoring, slots, now)
        per_day.append({"sp": sp, "points": plan.total, "starts": len(plan.assign)})
    return {"points": round(float(sum(d["points"] for d in per_day)), 1), "starts": sum(d["starts"] for d in per_day),
            "days": per_day}


def my_matchup(schedule_items: list[dict], matchup_period: int, team_id: int) -> dict | None:
    """Opponent and current points for this matchup period, if ESPN has generated the schedule."""
    for m in schedule_items:
        if m.get("matchupPeriodId") != matchup_period:
            continue
        home, away = m.get("home") or {}, m.get("away") or {}
        if team_id in (home.get("teamId"), away.get("teamId")):
            me, opp = (home, away) if home.get("teamId") == team_id else (away, home)

            def pts(side):
                return round(float(side.get("totalPoints") or sum((side.get("pointsByScoringPeriod") or {}).values())), 1)
            return {"opponent_id": opp.get("teamId"), "my_points": pts(me), "opp_points": pts(opp)}
    return None
