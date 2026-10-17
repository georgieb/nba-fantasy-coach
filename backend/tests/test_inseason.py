import datetime as dt

from app.data.espn_client import DEFAULT_SCORING, DEFAULT_SLOTS, Game, League, RosterSpot
from app.engine.lineup import daily_moves, per_game, plan_day
from app.engine.matchup import matchup_days, my_matchup, project_week
from app.engine.streaming import suggest_streams
from app.inseason import Season, briefing
from tests.test_engine import mk

START = dt.date(2026, 10, 20)
T0 = dt.datetime(2026, 10, 20, 19, 0).timestamp()  # tip-off times are relative to this


def game(sp, opp="OPP", hours=0.0):
    return Game(sp, START + dt.timedelta(days=sp - 1), opp, True, T0 + (sp - 1) * 86400 + hours * 3600)


def team_player(pid, team, pos, fp, injury="ACTIVE"):
    p = mk(pid, pos, fp)
    p.team, p.injury = team, injury
    return p


def roster_and_schedule():
    """10 starters' worth of players; team 'NOG' never plays, team 'AAA' plays sp 1-3, 'BBB' only sp 1."""
    sched = {"AAA": {sp: game(sp) for sp in (1, 2, 3)}, "BBB": {1: game(1)}}
    pos = [["PG"], ["SG"], ["SF"], ["PF"], ["C"], ["PG", "SG"], ["SF", "PF"], ["C"], ["PG"], ["SF"]]
    roster = [RosterSpot(team_player(i + 1, "AAA", pos[i], 3000 - i * 100), slot)
              for i, slot in enumerate(["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "UTIL", "UTIL"])]
    roster += [RosterSpot(team_player(20, "NOG", ["PG"], 3500), "BE"),            # no game ever
               RosterSpot(team_player(21, "BBB", ["C"], 2900, "OUT"), "BE"),      # out
               RosterSpot(team_player(22, "BBB", ["SG"], 3400), "IR")]            # on IR slot
    return roster, sched


def test_matchup_days_inferred_and_observed():
    w = matchup_days(167, 23)
    assert w[1] == list(range(1, 14)) and w[2] == list(range(14, 21)) and w[23] == list(range(161, 168))
    assert sum(len(v) for v in w.values()) == 167
    assert matchup_days(167, 23, {1: [14]})[1][-1] == 14  # observed days extend the inferred range


def test_plan_excludes_no_game_out_and_ir():
    roster, sched = roster_and_schedule()
    plan = plan_day(roster, 1, sched, DEFAULT_SCORING, DEFAULT_SLOTS)
    assert 20 not in plan.assign and 21 not in plan.assign and 22 not in plan.assign
    assert len(plan.assign) == 10 and sorted(plan.assign.values()) == sorted(
        ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "UTIL", "UTIL"])


def test_moves_start_and_bench():
    roster, sched = roster_and_schedule()
    roster[0] = RosterSpot(team_player(1, "NOG", ["PG"], 3000), "PG")   # starter with no game today
    sched["CCC"] = {1: game(1)}
    roster.append(RosterSpot(team_player(30, "CCC", ["PG"], 2500), "BE"))  # bench guy who plays
    plan = plan_day(roster, 1, sched, DEFAULT_SCORING, DEFAULT_SLOTS)
    out = daily_moves(roster, plan, DEFAULT_SCORING, now=0)
    acts = {(m["action"], m["player_id"]) for m in out["moves"]}
    assert ("bench", 1) in acts and ("start", 30) in acts
    assert next(m for m in out["moves"] if m["player_id"] == 1)["why"] == "no game today"


def test_locked_players_stay_put():
    roster, sched = roster_and_schedule()
    sched["CCC"] = {1: game(1, hours=-1)}  # already tipped off
    roster.append(RosterSpot(team_player(30, "CCC", ["PG"], 9999), "BE"))  # great player, but locked on the bench
    plan = plan_day(roster, 1, sched, DEFAULT_SCORING, DEFAULT_SLOTS, now=T0)
    assert 30 not in plan.assign
    out = daily_moves(roster, plan, DEFAULT_SCORING, now=T0)
    assert not any(m["player_id"] == 30 for m in out["moves"])


def test_questionable_starter_flagged_and_recent_form_blend():
    roster, sched = roster_and_schedule()
    roster[0].player.injury = "DAY_TO_DAY"
    out = daily_moves(roster, plan_day(roster, 1, sched, DEFAULT_SCORING, DEFAULT_SLOTS), DEFAULT_SCORING, now=0)
    assert any("day to day" in f for f in out["flags"])
    p = mk(99, ["PG"], 70 * 30)          # 30 FP/G projected
    assert per_game(p, DEFAULT_SCORING) == 30
    p.recent, p.recent_gp = {0: 50.0}, 5  # hot: 50 FP/G last 15
    assert per_game(p, DEFAULT_SCORING) == 36.0
    p.recent_gp = 1                       # too small a sample: ignored
    assert per_game(p, DEFAULT_SCORING) == 30


def test_project_week_and_streams():
    roster, sched = roster_and_schedule()
    days = [1, 2, 3]
    week = project_week(roster, days, sched, DEFAULT_SCORING, DEFAULT_SLOTS, now=0)
    assert week["starts"] == 30  # 10 AAA players x 3 games
    sched["DDD"] = {sp: game(sp) for sp in days}
    fa_good = team_player(40, "DDD", ["PG"], 5000)       # better than the worst starter, plays every day
    fa_idle = team_player(41, "NOG", ["PG"], 9000)       # no games left: never suggested
    already = roster[0].player                            # on my roster: never suggested
    st = suggest_streams(roster, [fa_good, fa_idle, already], days, sched, DEFAULT_SCORING, DEFAULT_SLOTS, now=0)
    assert [s["add"]["player_id"] for s in st] == [40]
    assert st[0]["gain"] > 0 and st[0]["drop"]["player_id"] in {20, 21, 10}


def test_my_matchup_parse():
    items = [{"matchupPeriodId": 1, "home": {"teamId": 2, "totalPoints": 120.5}, "away": {"teamId": 7, "totalPoints": 99}}]
    assert my_matchup(items, 1, 2) == {"opponent_id": 7, "my_points": 120.5, "opp_points": 99.0}
    assert my_matchup(items, 2, 2) is None


def _season(roster, sched, preseason=False):
    lg = League("1", 2027, "T", {2: "Me", 7: "Them"}, [2, 7], dict(DEFAULT_SLOTS), dict(DEFAULT_SCORING), 13)
    status = {"final_sp": 167, "matchup_count": 23,
              "schedule": [{"matchupPeriodId": 1, "home": {"teamId": 2}, "away": {"teamId": 7}}]}

    class NoFA:
        def free_agents(self, sp, limit=60):
            return []
    return Season(lg, 2, sched, {2: roster, 7: roster[:5]}, status, 1, 0.0, NoFA(), preseason)


def test_briefing_headline_and_idle_states():
    roster, sched = roster_and_schedule()
    b = briefing(_season(roster, sched))
    assert not b["idle"] and "lineup move" in b["headline"] and "vs Them" in b["headline"]
    assert briefing(_season(roster, sched, preseason=True))["idle"]
    assert "No roster yet" in briefing(_season([], sched))["headline"]


def test_streams_never_drop_a_clearly_better_player(monkeypatch):
    from app.engine import streaming
    roster, sched = roster_and_schedule()
    days = [1, 2, 3]
    sched["DDD"] = {sp: game(sp) for sp in days}
    for s in roster:            # strong roster with no games this week -> any pickup with games adds points
        s.player.team, s.player.injury, s.slot = "NOG", "ACTIVE", ("BE" if s.slot == "IR" else s.slot)
    weak_fa = team_player(50, "DDD", ["PG"], 1500)  # ~21 FP/G, 3 games: big weekly gain, much worse per game
    args = (roster, [weak_fa], days, sched, DEFAULT_SCORING, DEFAULT_SLOTS)
    assert suggest_streams(*args, now=0) == []
    monkeypatch.setattr(streaming, "MAX_DROP_RATIO", 100.0)   # without the rule the swap would be suggested
    assert [x["add"]["player_id"] for x in suggest_streams(*args, now=0)] == [50]
