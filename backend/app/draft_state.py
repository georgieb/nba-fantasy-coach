"""In-memory draft session shared by ESPN sync, manual entry and the mock simulator."""
import threading

from app.data.espn_client import League, Player
from app.engine.snake import team_for_pick
from app.engine.valuation import fantasy_points


class DraftState:
    def __init__(self, league: League, players: list[Player], my_team_id: int, source: str):
        self.league = league
        self.players = {p.id: p for p in players}
        self.fpts = {p.id: fantasy_points(p, league.scoring) for p in players}
        self.my_team_id = my_team_id
        self.source = source            # "espn" | "sim"
        self.picks: list[dict] = []     # [{pick, team_id, player_id}]
        self.lock = threading.RLock()
        self.last_sync_error: str | None = None

    # ---------- derived ----------
    @property
    def total_picks(self) -> int:
        return len(self.league.pick_order) * self.league.rounds

    @property
    def current_pick(self) -> int:
        return len(self.picks) + 1

    @property
    def complete(self) -> bool:
        return len(self.picks) >= self.total_picks

    @property
    def on_clock(self) -> int | None:
        return None if self.complete else team_for_pick(self.current_pick, self.league.pick_order)

    def drafted(self) -> set[int]:
        return {p["player_id"] for p in self.picks}

    def available(self) -> list[int]:
        taken = self.drafted()
        return [i for i in self.players if i not in taken]

    def rosters(self) -> dict[int, list[int]]:
        out = {t: [] for t in self.league.pick_order}
        for p in self.picks:
            if p["player_id"] in self.players:
                out.setdefault(p["team_id"], []).append(p["player_id"])
        return out

    # ---------- mutations ----------
    def add_pick(self, player_id: int, team_id: int | None = None):
        with self.lock:
            if self.complete:
                raise ValueError("Draft is complete")
            if player_id in self.drafted():
                raise ValueError("Player already drafted")
            if player_id not in self.players:
                raise ValueError("Unknown player id")
            self.picks.append({"pick": self.current_pick, "team_id": team_id or self.on_clock, "player_id": player_id})

    def undo(self):
        with self.lock:
            if self.picks:
                self.picks.pop()

    def replace_picks(self, picks: list[dict]):
        with self.lock:
            self.picks = list(picks)  # players outside our pool still consume the pick
