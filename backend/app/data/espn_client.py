"""Thin client over ESPN's fantasy basketball (fba) read API.

ESPN season ids are the year the season ends (2026-27 season -> 2027).
"""
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

from app.config import CACHE_DIR

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{year}"

# ESPN stat ids -> names (subset we score on)
STAT_NAMES = {
    0: "PTS", 1: "BLK", 2: "STL", 3: "AST", 4: "OREB", 5: "DREB", 6: "REB",
    11: "TO", 13: "FGM", 14: "FGA", 15: "FTM", 16: "FTA", 17: "3PM", 18: "3PA",
    37: "DD", 38: "TD", 40: "MIN", 42: "GP",
}
# ESPN default H2H points scoring (verified: Jokic 2027 projection = 4838)
DEFAULT_SCORING = {0: 1, 17: 1, 14: -1, 13: 2, 16: -1, 15: 1, 6: 1, 3: 2, 2: 4, 1: 4, 11: -2}

# ESPN lineup slot ids
SLOT_NAMES = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF",
              8: "G/F", 9: "PF/C", 10: "F/C", 11: "UTIL", 12: "BE", 13: "IR"}
BASE_POS = ["PG", "SG", "SF", "PF", "C"]
DEFAULT_SLOTS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UTIL": 3, "BE": 3, "IR": 3}

PRO_TEAMS = {0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
             9: "GSW", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN",
             17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI", 21: "PHX", 22: "POR", 23: "SAC", 24: "SAS",
             25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR", 29: "MEM", 30: "CHA"}


@dataclass
class Player:
    id: int
    name: str
    team: str
    positions: list[str]          # base positions (PG..C) the player is eligible at
    proj: dict[str, float]        # projected season totals by stat name
    proj_raw: dict[int, float]    # same, keyed by ESPN stat id (for scoring)
    gp: float
    adp: float | None
    rank: int | None
    injury: str
    outlook: str = ""
    recent: dict[int, float] | None = None  # last-15-day per-game averages (ESPN stat id -> value)
    recent_gp: float = 0.0


@dataclass
class Game:
    sp: int              # ESPN scoring period (one per calendar day)
    date: dt.date        # US/Eastern date of the game
    opp: str
    home: bool
    start: float         # tip-off, epoch seconds (lineups lock per player at tip-off)


@dataclass
class RosterSpot:
    player: Player
    slot: str            # current ESPN lineup slot name (PG..UTIL, BE, IR)


@dataclass
class League:
    league_id: str | None
    year: int
    name: str
    teams: dict[int, str]                       # team_id -> name
    pick_order: list[int]                       # team ids in round-1 order
    slots: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SLOTS))
    scoring: dict[int, float] = field(default_factory=lambda: dict(DEFAULT_SCORING))
    rounds: int = 13
    my_team_id: int | None = None   # detected from SWID ownership when cookies are set


class EspnClient:
    def __init__(self, year: int, league_id: str | None = None, s2: str | None = None, swid: str | None = None):
        self.year = year
        self.league_id = league_id
        self.session = requests.Session()
        self.swid = swid
        if s2 and swid:
            self.session.cookies.set("espn_s2", s2)
            self.session.cookies.set("SWID", swid)

    # ---------- low level ----------
    def _get(self, path: str, params=None, filt: dict | None = None):
        headers = {"x-fantasy-filter": json.dumps(filt)} if filt else {}
        r = self.session.get(BASE.format(year=self.year) + path, params=params, headers=headers, timeout=20)
        if r.status_code in (401, 403):
            raise PermissionError("ESPN denied access - this league is private: add your espn_s2 and SWID cookies")
        if r.status_code == 404:
            raise LookupError(f"League not found - check the League ID and season ({self.year})")
        r.raise_for_status()
        return r.json()

    def _league_path(self) -> str:
        if not self.league_id:
            raise ValueError("ESPN_LEAGUE_ID not set")
        return f"/segments/0/leagues/{self.league_id}"

    # ---------- players ----------
    def players(self, limit: int = 600, use_cache: bool = True) -> list[Player]:
        cache = CACHE_DIR / f"players_{self.year}.json"
        if use_cache and cache.exists() and time.time() - cache.stat().st_mtime < 6 * 3600:
            raw = json.loads(cache.read_text())
        else:
            filt = {"filterActive": {"value": True}, "limit": limit,
                    "sortDraftRanks": {"sortPriority": 1, "sortAsc": True, "value": "STANDARD"}}
            raw = self._get("/players", params={"view": "kona_player_info", "scoringPeriodId": 0}, filt=filt)
            cache.write_text(json.dumps(raw))
        return [p for p in (parse_player(d, self.year) for d in raw) if p]

    # ---------- league ----------
    def league(self) -> League:
        d = self._get(self._league_path(), params={"view": ["mTeam", "mSettings"]})
        s = d.get("settings", {})
        teams = {t["id"]: (t.get("name") or f'{t.get("location", "")} {t.get("nickname", "")}'.strip() or t.get("abbrev", str(t["id"])))
                 for t in d.get("teams", [])}
        slot_counts = s.get("rosterSettings", {}).get("lineupSlotCounts", {})
        slots: dict[str, int] = {}
        for sid, n in slot_counts.items():
            name = SLOT_NAMES.get(int(sid))
            if name and n:
                slots[name] = slots.get(name, 0) + n
        scoring = {it["statId"]: it["points"] for it in s.get("scoringSettings", {}).get("scoringItems", [])}
        pick_order = s.get("draftSettings", {}).get("pickOrder") or sorted(teams)
        rounds = sum(n for k, n in slots.items() if k != "IR") or 13
        mine = next((t["id"] for t in d.get("teams", []) if self.swid and self.swid in (t.get("owners") or [])), None)
        return League(self.league_id, self.year, s.get("name", "ESPN League"), teams, pick_order,
                      slots or dict(DEFAULT_SLOTS), scoring or dict(DEFAULT_SCORING), rounds, mine)

    # ---------- in-season (all read-only GETs) ----------
    def pro_schedule(self) -> dict[str, dict[int, Game]]:
        """NBA schedule: team abbrev -> {scoring period -> Game}. Cached per day."""
        cache = CACHE_DIR / f"pro_schedule_{self.year}_{dt.date.today().isoformat()}.json"
        if cache.exists():
            raw = json.loads(cache.read_text())
        else:
            raw = self._get("", params={"view": "proTeamSchedules_wl"})["settings"]["proTeams"]
            for old in CACHE_DIR.glob(f"pro_schedule_{self.year}_*.json"):
                old.unlink()
            cache.write_text(json.dumps(raw))
        et = ZoneInfo("America/New_York")
        out: dict[str, dict[int, Game]] = {}
        for t in raw:
            me = PRO_TEAMS.get(t["id"], t.get("abbrev", "?"))
            for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
                g = games[0]
                opp_id = g["awayProTeamId"] if g["homeProTeamId"] == t["id"] else g["homeProTeamId"]
                out.setdefault(me, {})[int(sp)] = Game(int(sp), dt.datetime.fromtimestamp(g["date"] / 1000, et).date(),
                                                       PRO_TEAMS.get(opp_id, "?"), g["homeProTeamId"] == t["id"], g["date"] / 1000)
        return out

    def rosters(self) -> dict[int, list[RosterSpot]]:
        """Every team's roster with current lineup slots."""
        d = self._get(self._league_path(), params={"view": "mRoster"})
        out = {}
        for t in d.get("teams", []):
            spots = []
            for e in (t.get("roster") or {}).get("entries", []):
                p = parse_player(e["playerPoolEntry"]["player"], self.year)
                if p:
                    spots.append(RosterSpot(p, SLOT_NAMES.get(e.get("lineupSlotId"), "BE")))
            out[t["id"]] = spots
        return out

    def free_agents(self, scoring_period: int, limit: int = 60) -> list[Player]:
        filt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "limit": limit,
                            "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
        d = self._get(self._league_path(), params={"view": "kona_player_info", "scoringPeriodId": scoring_period}, filt=filt)
        return [p for p in (parse_player(x["player"], self.year) for x in d.get("players", [])) if p]

    def season_status(self) -> dict:
        """Current matchup period, final scoring period, matchup schedule (opponents/points)."""
        d = self._get(self._league_path(), params={"view": ["mStatus", "mSettings", "mMatchupScore"]})
        ss = d.get("settings", {}).get("scheduleSettings", {})
        st = d.get("status", {})
        return {"current_matchup": st.get("currentMatchupPeriod", 1), "final_sp": st.get("finalScoringPeriod"),
                "matchup_count": len(ss.get("matchupPeriods", {})) or ss.get("matchupPeriodCount", 0),
                "schedule": d.get("schedule", [])}

    def draft_picks(self) -> list[dict]:
        """Completed picks so far: [{pick, round, team_id, player_id}]"""
        d = self._get(self._league_path(), params={"view": "mDraftDetail"})
        out = []
        for p in d.get("draftDetail", {}).get("picks", []):
            if p.get("playerId", -1) > 0:
                out.append({"pick": p["overallPickNumber"], "round": p.get("roundId"),
                            "team_id": p["teamId"], "player_id": p["playerId"]})
        return sorted(out, key=lambda x: x["pick"])


def parse_player(d: dict, year: int) -> Player | None:
    proj_split = next((s for s in d.get("stats", []) if s.get("id") == f"10{year}"), None)
    if not proj_split or not proj_split.get("stats"):
        return None
    raw = {int(k): float(v) for k, v in proj_split["stats"].items()}
    positions = [BASE_POS[s] for s in d.get("eligibleSlots", []) if s < 5]
    if not positions:
        return None
    own = d.get("ownership") or {}
    rank = (d.get("draftRanksByRankType") or {}).get("STANDARD", {}).get("rank")
    last15 = next((s for s in d.get("stats", []) if s.get("id") == f"02{year}" and s.get("averageStats")), None)
    return Player(
        id=d["id"], name=d["fullName"], team=PRO_TEAMS.get(d.get("proTeamId", 0), "FA"),
        positions=positions,
        proj={STAT_NAMES[k]: v for k, v in raw.items() if k in STAT_NAMES},
        proj_raw=raw, gp=raw.get(42, 0.0),
        adp=own.get("averageDraftPosition") or None, rank=rank,
        injury=d.get("injuryStatus", "ACTIVE"), outlook=d.get("seasonOutlook", "") or "",
        recent={int(k): float(v) for k, v in last15["averageStats"].items()} if last15 else None,
        recent_gp=float((last15 or {}).get("stats", {}).get("42", 0) or 0),
    )
