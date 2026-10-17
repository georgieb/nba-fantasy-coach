"""In-season views shared by the API and the daily briefing script. Read-only against ESPN."""
import datetime as dt
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.data.espn_client import EspnClient, Game, League, RosterSpot
from app.engine.lineup import QUESTIONABLE, daily_moves, plan_day
from app.engine.matchup import matchup_days, my_matchup, observed_days, project_week, scoring_period_for, season_start
from app.engine.streaming import suggest_streams

ET = ZoneInfo("America/New_York")


@dataclass
class Season:
    league: League
    team_id: int
    schedule: dict[str, dict[int, Game]]
    rosters: dict[int, list[RosterSpot]]
    status: dict
    today_sp: int
    now: float
    client: EspnClient
    preseason: bool = False

    @property
    def roster(self) -> list[RosterSpot]:
        return self.rosters.get(self.team_id, [])

    @property
    def weeks(self) -> dict[int, list[int]]:
        return matchup_days(self.status["final_sp"] or 167, self.status["matchup_count"] or 23,
                            observed_days(self.status["schedule"]))

    def current_matchup_period(self) -> int:
        for mp, sps in self.weeks.items():
            if self.today_sp <= sps[-1]:
                return mp
        return max(self.weeks)

    def days_left(self) -> list[int]:
        return [sp for sp in self.weeks[self.current_matchup_period()] if sp >= self.today_sp]


def load_season(client: EspnClient, team_id: int, now: float | None = None) -> Season:
    now = now or time.time()
    league = client.league()
    schedule = client.pro_schedule()
    today = dt.datetime.fromtimestamp(now, ET).date()
    start = season_start(schedule)
    return Season(league, team_id, schedule, client.rosters(), client.season_status(),
                  max(1, scoring_period_for(today, start)), now, client, preseason=today < start)


def sp_date(s: Season, sp: int) -> str:
    return (season_start(s.schedule) + dt.timedelta(days=sp - 1)).isoformat()


def lineup_view(s: Season, sp: int | None = None) -> dict:
    sp = sp or s.today_sp
    plan = plan_day(s.roster, sp, s.schedule, s.league.scoring, s.league.slots, s.now)
    return {"date": sp_date(s, sp), **daily_moves(s.roster, plan, s.league.scoring, s.now),
            "roster_size": len(s.roster)}


def week_view(s: Season) -> dict:
    mp = s.current_matchup_period()
    days = s.weeks[mp]
    grid = []
    for spot in s.roster:
        cells = []
        for sp in days:
            g = s.schedule.get(spot.player.team, {}).get(sp)
            cells.append({"sp": sp, "game": f"{'vs' if g.home else '@'} {g.opp}" if g else None})
        grid.append({"player_id": spot.player.id, "name": spot.player.name, "team": spot.player.team,
                     "positions": spot.player.positions, "games": sum(1 for c in cells if c["game"]),
                     "games_left": sum(1 for c in cells if c["game"] and c["sp"] >= s.today_sp), "cells": cells})
    grid.sort(key=lambda r: -r["games_left"])
    starts = {}
    for sp in days:
        if sp >= s.today_sp:
            starts[sp] = sorted(plan_day(s.roster, sp, s.schedule, s.league.scoring, s.league.slots, s.now).assign)
    return {"matchup_period": mp, "days": [{"sp": sp, "date": sp_date(s, sp)} for sp in days], "today_sp": s.today_sp,
            "grid": grid, "starts": {str(k): v for k, v in starts.items()}}


def matchup_view(s: Season) -> dict:
    mp, days = s.current_matchup_period(), s.days_left()
    mine = project_week(s.roster, days, s.schedule, s.league.scoring, s.league.slots, s.now)
    m = my_matchup(s.status["schedule"], mp, s.team_id)
    out = {"matchup_period": mp, "days_left": len(days), "me": {"name": s.league.teams.get(s.team_id), "current": 0.0,
                                                                "remaining": mine["points"], "starts_left": mine["starts"]}}
    if m and m["opponent_id"] in s.rosters:
        theirs = project_week(s.rosters[m["opponent_id"]], days, s.schedule, s.league.scoring, s.league.slots, s.now)
        out["me"]["current"] = m["my_points"]
        out["opponent"] = {"name": s.league.teams.get(m["opponent_id"]), "current": m["opp_points"],
                           "remaining": theirs["points"], "starts_left": theirs["starts"]}
        out["projected_margin"] = round((m["my_points"] + mine["points"]) - (m["opp_points"] + theirs["points"]), 1)
    return out


def streams_view(s: Season, top: int = 5) -> list[dict]:
    fas = s.client.free_agents(s.today_sp)
    return suggest_streams(s.roster, fas, s.days_left(), s.schedule, s.league.scoring, s.league.slots, s.now, top)


def briefing(s: Season) -> dict:
    """Everything the daily notification needs, in one dict (also cached for the lineup page)."""
    if s.preseason or not s.roster:
        start = season_start(s.schedule)
        why = (f"Season starts {start:%a %b %-d} - first lineup on opening day" if s.preseason
               else "No roster yet - lineups start after your draft")
        return {"generated_at": dt.datetime.fromtimestamp(s.now, ET).isoformat(timespec="minutes"),
                "date": dt.datetime.fromtimestamp(s.now, ET).date().isoformat(), "headline": why, "moves": [],
                "flags": [], "injuries": [], "streams": [], "matchup": None, "games_today": 0,
                "roster_size": len(s.roster), "idle": True}
    lu, mu = lineup_view(s), matchup_view(s)
    st = streams_view(s, top=2) if s.roster else []
    injured = [f"{r['name']} ({r['injury'].replace('_', ' ').lower()})" for r in lu["lineup"]
               if r["injury"] in QUESTIONABLE or r["injury"] == "OUT"]
    parts = [f"{len(lu['moves'])} lineup move{'s' if len(lu['moves']) != 1 else ''}"]
    if injured:
        parts.append(f"{len(injured)} injury flag{'s' if len(injured) != 1 else ''}")
    if st:
        parts.append(f"+{st[0]['gain']:.0f} pt stream")
    if "projected_margin" in mu:
        parts.append(f"proj {'+' if mu['projected_margin'] >= 0 else ''}{mu['projected_margin']:.0f} vs {mu['opponent']['name']}")
    return {"generated_at": dt.datetime.fromtimestamp(s.now, ET).isoformat(timespec="minutes"), "date": lu["date"],
            "headline": " · ".join(parts), "moves": lu["moves"], "flags": lu["flags"], "injuries": injured,
            "streams": st, "matchup": mu, "games_today": lu["games_today"], "roster_size": lu["roster_size"],
            "idle": False}
