"""ESPN polling etiquette: stop when the draft ends, pause when the page is closed, back off on errors."""
import pytest

from app import main
from app.auth import Identity
from app.data.espn_client import DEFAULT_SCORING, League
from app.draft_state import DraftState
from tests.test_api import PLAYERS

SLOTS = {"PG": 1, "C": 1, "BE": 0}  # tiny league: 2 teams x 2 rounds = 4 picks


class FakeEspn:
    def __init__(self):
        self.calls, self.picks, self.fail = 0, [], 0

    def draft_picks(self):
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise RuntimeError("429 Too Many Requests")
        return list(self.picks)


@pytest.fixture
def live(monkeypatch):
    main.SESSIONS.clear()
    monkeypatch.setattr(main.config, "POLL_SECONDS", 3)
    monkeypatch.setattr(main, "persist", lambda s: None)
    lg = League("1", 2027, "T", {1: "A", 2: "B"}, [1, 2], dict(SLOTS), dict(DEFAULT_SCORING), 2)
    s = main.Session(Identity(key="user:u1", user_id="u1"), client=FakeEspn(),
                     state=DraftState(lg, PLAYERS, 1, "espn"), polling=True, last_seen=1000.0)
    main.SESSIONS[s.ident.key] = s
    yield s
    main.SESSIONS.clear()


def pick(n):
    return {"pick": n, "team_id": 1 if n in (1, 4) else 2, "player_id": n}


def test_polls_while_page_open(live):
    main.poll_tick(1001)
    main.poll_tick(1004)
    assert live.client.calls == 2


def test_stops_when_draft_complete(live):
    live.client.picks = [pick(n) for n in range(1, 5)]
    main.poll_tick(1001)
    assert live.state.complete and live.polling is False
    main.poll_tick(1004)
    assert live.client.calls == 1  # no more ESPN calls after the last pick


def test_pauses_when_page_closed_and_resumes(live):
    main.poll_tick(1000 + main.IDLE_PAUSE + 1)  # nobody has loaded the page for > 5 min
    assert live.client.calls == 0 and live.polling  # paused, not switched off
    live.last_seen = 2000.0                         # page polls again
    main.poll_tick(2001)
    assert live.client.calls == 1


def test_backs_off_on_errors_then_recovers(live, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(main.time, "time", lambda: clock[0])
    live.client.fail = 4
    waits = []
    for _ in range(4):
        main.poll_tick(clock[0])
        waits.append(live.next_sync_at - clock[0])
        main.poll_tick(clock[0] + 1)          # inside the backoff window: no call
        clock[0] = live.next_sync_at
        live.last_seen = clock[0]
    assert waits == [6, 12, 24, 30] and live.client.calls == 4
    assert "retrying in 30s" in live.state.last_sync_error
    main.poll_tick(clock[0])                  # ESPN healthy again
    assert live.sync_failures == 0 and live.next_sync_at == 0 and live.state.last_sync_error is None
