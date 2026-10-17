from datetime import date

from app.data.espn_client import DEFAULT_SCORING, DEFAULT_SLOTS, Player
from app.engine import esoteric
from app.engine.roster_fit import best_lineup, open_slots, starter_slots
from app.engine.snake import next_pick_for_team, picks_for_team, survival, team_for_pick
from app.engine.valuation import fantasy_points, replacement_levels
from app.sim.mock import advance_to_me, new_sim
from app.engine.valuation import recommend


def mk(pid, pos, fp, adp=None):
    # PTS-only projection so fantasy points == fp under default scoring
    return Player(pid, f"P{pid}", "XXX", pos, {"PTS": fp}, {0: fp, 42: 70}, 70, adp or pid, pid, "ACTIVE")


def test_fantasy_points_matches_espn_jokic():
    raw = {0: 2034, 1: 58, 2: 115, 3: 727, 6: 914, 11: 245, 13: 762, 14: 1325, 15: 388, 16: 475, 17: 122}
    p = Player(1, "Jokic", "DEN", ["C"], {}, raw, 72, 1.4, 1, "ACTIVE")
    assert fantasy_points(p, DEFAULT_SCORING) == 4838


def test_snake_order():
    order = list(range(1, 11))
    assert picks_for_team(3, order, 4) == [3, 18, 23, 38]
    assert team_for_pick(10, order) == 10 and team_for_pick(11, order) == 10 and team_for_pick(20, order) == 1
    assert next_pick_for_team(3, order, 13, 3) == 18


def test_survival_monotonic():
    assert survival(50, 10, 15) > survival(50, 10, 45) > survival(50, 10, 80)
    assert survival(50, 10, None) == 0.0


def test_slots_and_lineup():
    slots = starter_slots(DEFAULT_SLOTS)
    assert len(slots) == 10 and slots[-3:] == ["UTIL"] * 3
    roster = [(1, ["C"], 100), (2, ["C"], 90), (3, ["C"], 80), (4, ["C"], 70), (5, ["C"], 60)]
    total, assign = best_lineup(roster, slots)
    assert total == 100 + 90 + 80 + 70  # C + 3 UTIL; 5th center can't start
    assert 5 not in assign
    assert "PG" in open_slots(roster, slots) and "C" not in open_slots(roster, slots)


def test_replacement_levels_reflect_scarcity():
    players = {i: mk(i, ["C"], 1000 - i * 10) for i in range(1, 6)}
    players.update({i: mk(i, ["PG"], 1000 - (i - 100) * 2) for i in range(100, 130)})
    fp = {i: p.proj_raw[0] for i, p in players.items()}
    repl = replacement_levels(list(players), [["C", "PG"], ["C", "PG"]], players, fp)
    assert repl["C"] == fp[3] and repl["PG"] == fp[102]


def test_esoteric():
    d = date(1995, 2, 19)  # Jokic
    assert esoteric.life_path(d) == 9  # 1+9+9+5+0+2+1+9 = 36 -> 9
    assert esoteric.zodiac(d) == ("Pig", "Wood")
    assert esoteric.zodiac(date(1995, 1, 30)) == ("Dog", "Wood")  # before Lunar New Year
    c = esoteric.compatibility(d, date(1983, 6, 1), today=date(2026, 10, 20))
    assert -1 <= c["score"] <= 1


def test_full_mock_draft_fills_valid_lineup():
    pos_cycle = [["PG"], ["SG"], ["SF"], ["PF"], ["C"], ["PG", "SG"], ["SF", "PF"], ["PF", "C"]]
    players = [mk(i, pos_cycle[i % len(pos_cycle)], 3000 - i * 8, adp=i) for i in range(1, 260)]
    st = new_sim(players, teams=10, my_slot=4, seed=7)
    advance_to_me(st)
    while not st.complete:
        rec = recommend(st, top=5)
        st.add_pick(rec["recommendations"][0]["player_id"])
        advance_to_me(st)
    mine = st.rosters()[4]
    assert len(mine) == 13
    slots = starter_slots(DEFAULT_SLOTS)
    _, assign = best_lineup([(i, st.players[i].positions, st.fpts[i]) for i in mine], slots)
    assert len(assign) == 10  # every starting slot filled with an eligible player
