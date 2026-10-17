import pytest
from fastapi.testclient import TestClient

from app import main
from tests.test_engine import mk

POS = [["PG"], ["SG"], ["SF"], ["PF"], ["C"], ["PG", "SG"], ["SF", "PF"], ["PF", "C"]]
PLAYERS = [mk(i, POS[i % len(POS)], 3000 - i * 8, adp=i) for i in range(1, 260)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "players", lambda year: PLAYERS)
    main.SESSIONS.clear()
    return TestClient(main.app)


def h(sid):
    return {"X-Session-Id": sid}


def test_session_header_required(client):
    assert client.get("/api/state").status_code == 401


def test_sessions_are_isolated(client):
    a = client.post("/api/sim/start", json={"teams": 8, "my_slot": 3}, headers=h("session-aaaa")).json()
    client.post("/api/sim/start", json={"teams": 12, "my_slot": 1}, headers=h("session-bbbb"))
    client.post("/api/pick", json={"player_id": a["picks"][0]["player_id"] + 100}, headers=h("session-aaaa"))
    sa = client.get("/api/state", headers=h("session-aaaa")).json()
    sb = client.get("/api/state", headers=h("session-bbbb")).json()
    assert len(sa["pick_order"]) == 8 and len(sb["pick_order"]) == 12
    assert sa["current_pick"] != sb["current_pick"]
    assert client.get("/api/state", headers=h("session-cccc")).status_code == 400  # fresh session has no draft


def test_advice_without_user_key_falls_back(client):
    client.post("/api/sim/start", json={}, headers=h("session-aaaa"))
    r = client.post("/api/advice", headers=h("session-aaaa")).json()  # guest, no X-Anthropic-Key
    assert r["source"] == "engine" and "Profile page" in r["error"]


def test_guest_cannot_use_espn(client):
    g = h("session-guest")
    assert client.post("/api/league/lookup", json={"espn_league_id": "1"}, headers=g).status_code == 401
    assert client.post("/api/espn/connect", json={"league_id": "x"}, headers=g).status_code == 401


def test_input_caps(client):
    g = h("session-caps")
    client.post("/api/sim/start", json={}, headers=g)
    assert client.get("/api/players?limit=100000", headers=g).status_code == 422
    assert client.get("/api/players?q=" + "x" * 51, headers=g).status_code == 422
    assert client.get("/api/players?pos=DROP%20TABLE", headers=g).status_code == 422
    assert client.get("/api/recommend?top=1000", headers=g).status_code == 422
    assert len(client.get("/api/players?limit=300", headers=g).json()) <= 300


def test_guest_sessions_are_capped(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_GUEST_SESSIONS", 5)
    for i in range(12):
        client.get("/api/me", headers=h(f"guest-session-{i:04d}"))
    assert sum(k.startswith("guest:") for k in main.SESSIONS) <= 5
    assert "guest:guest-session-0011" in main.SESSIONS  # newest kept
