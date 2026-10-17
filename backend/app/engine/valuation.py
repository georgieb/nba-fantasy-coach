"""Draft valuation: projected fantasy points, live replacement levels (VORP),
roster fit, and value-over-next-available (VONA) using snake look-ahead."""
from collections import Counter

from app.data.espn_client import Player
from app.engine.roster_fit import SLOT_ELIG, best_lineup, eligible, open_slots, roster_size, starter_slots
from app.engine.snake import next_pick_for_team, survival

W_VORP, W_VONA = 0.6, 0.4
BENCH_FLOOR = 0.35  # value kept by a pick that only makes the bench (injury/streaming insurance)


def fantasy_points(p: Player, scoring: dict[int, float]) -> float:
    return round(sum(p.proj_raw.get(stat, 0.0) * w for stat, w in scoring.items()), 1)


def _entries(ids, players, fpts):
    return [(i, players[i].positions, fpts[i]) for i in ids]


def replacement_levels(available: list[int], open_by_team: list[list[str]], players, fpts) -> dict[str, float]:
    """Greedily fill every team's open starter slots with the best available players
    (most restrictive slot first). Replacement level at a position = best player left over."""
    demand = Counter(s for slots in open_by_team for s in slots)
    ranked = sorted(available, key=lambda i: -fpts[i])
    leftover = []
    for i in ranked:
        if sum(demand.values()) == 0:
            leftover.append(i)
            continue
        fits = [s for s in demand if demand[s] > 0 and eligible(players[i].positions, s)]
        if fits:
            demand[min(fits, key=lambda s: len(SLOT_ELIG[s]))] -= 1
        else:
            leftover.append(i)
    repl = {}
    for pos in ["PG", "SG", "SF", "PF", "C"]:
        repl[pos] = next((fpts[i] for i in leftover if pos in players[i].positions), 0.0)
    return repl


def expected_best_at(slot: str, cands: list[int], exclude: int, players, fpts, adp_of, cur: int, nxt: int | None) -> float:
    """E[best fpts available at our next pick among players eligible for `slot`]."""
    exp, p_none = 0.0, 1.0
    n = 0
    for i in cands:
        if i == exclude or not eligible(players[i].positions, slot):
            continue
        s = survival(adp_of(i), cur, nxt)
        exp += fpts[i] * s * p_none
        p_none *= 1 - s
        n += 1
        if p_none < 0.01 or n >= 25:
            break
    return exp


def recommend(state, top: int = 10, astro_scores: dict[int, float] | None = None, astro_weight: float = 0.0) -> dict:
    lg = state.league
    players, fpts = state.players, state.fpts
    slots = starter_slots(lg.slots)
    rosters = state.rosters()
    available = state.available()
    me = state.my_team_id
    cur = state.current_pick
    my_next = cur if state.on_clock == me else next_pick_for_team(me, lg.pick_order, lg.rounds, cur)
    after_that = next_pick_for_team(me, lg.pick_order, lg.rounds, my_next) if my_next else None

    open_by_team = {t: open_slots(_entries(ids, players, fpts), slots) for t, ids in rosters.items()}
    repl = replacement_levels(available, list(open_by_team.values()), players, fpts)

    my_ids = rosters.get(me, [])
    base_total, _ = best_lineup(_entries(my_ids, players, fpts), slots)
    bench_room = len(my_ids) < roster_size(lg.slots)

    def adp_of(i):
        return players[i].adp or players[i].rank

    pool = sorted(available, key=lambda i: -fpts[i])[:150]
    rows = []
    for i in pool:
        p = players[i]
        total, assign = best_lineup(_entries(my_ids + [i], players, fpts), slots)
        gain = total - base_total
        slot = assign.get(i, "BE")
        need = max(gain / fpts[i] if fpts[i] > 0 else 0.0, BENCH_FLOOR if bench_room else 0.0)
        vorp = fpts[i] - min(repl[pos] for pos in p.positions)
        # VONA: what we lose at this slot if we wait until our following pick
        fill_slot = slot if slot != "BE" else "UTIL"
        nxt_best = expected_best_at(fill_slot, pool, i, players, fpts, adp_of, my_next or cur, after_that)
        vona = fpts[i] - nxt_best if after_that else vorp
        score = (W_VORP * vorp + W_VONA * vona) * need
        astro = (astro_scores or {}).get(i, 0.0)
        if astro_weight:
            score *= 1 + astro_weight * astro
        rows.append({
            "player_id": i, "name": p.name, "team": p.team, "positions": p.positions,
            "fpts": fpts[i], "fpg": round(fpts[i] / p.gp, 1) if p.gp else 0.0, "gp": p.gp,
            "adp": p.adp, "injury": p.injury, "slot": slot, "need": round(need, 2),
            "vorp": round(vorp, 1), "vona": round(vona, 1), "astro": round(astro, 2),
            "survive_next": round(survival(adp_of(i), cur, after_that if state.on_clock == me else my_next), 2),
            "score": round(score, 1),
        })
    rows.sort(key=lambda r: (-r["score"], -r["fpts"]))

    # Build plan: which of my open slots lose the most value if I wait
    my_open = open_by_team.get(me, slots)
    plan = []
    for s in dict.fromkeys(my_open):
        best_now = next((i for i in pool if eligible(players[i].positions, s)), None)
        if best_now is None:
            continue
        later = expected_best_at(s, pool, -1, players, fpts, adp_of, cur, my_next if state.on_clock != me else after_that)
        plan.append({"slot": s, "open": my_open.count(s), "best_now": players[best_now].name,
                     "best_now_fpts": fpts[best_now], "expected_later_fpts": round(later, 1),
                     "dropoff": round(fpts[best_now] - later, 1)})
    plan.sort(key=lambda r: -r["dropoff"])

    return {
        "current_pick": cur, "on_clock": state.on_clock, "my_next_pick": my_next,
        "my_following_pick": after_that, "picks_until_me": (my_next - cur) if my_next else None,
        "replacement": repl, "recommendations": rows[:top], "build_plan": plan,
        "my_open_slots": my_open, "opponent_open_slots": {t: s for t, s in open_by_team.items() if t != me},
    }
