"""Integration tests against local Supabase (`supabase start`). ESPN is mocked."""
import uuid
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient
from supabase import create_client

from app import config, db, main
from app.data.espn_client import DEFAULT_SCORING, DEFAULT_SLOTS, EspnClient, League
from tests.test_api import PLAYERS


def _anon_key():
    env = Path(__file__).resolve().parents[2] / "frontend" / ".env.local"
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith("NEXT_PUBLIC_SUPABASE_ANON_KEY="):
            return line.split("=", 1)[1]


def _up():
    try:
        return config.ACCOUNTS_ENABLED and requests.get(f"{config.SUPABASE_URL}/auth/v1/health",
                                                        headers={"apikey": _anon_key() or ""}, timeout=5).ok
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(not _up() or not _anon_key(), reason="local Supabase not running")

ESPN_CALLS = []


def fake_league(self):
    ESPN_CALLS.append((self.league_id, self.session.cookies.get("espn_s2"), self.swid))
    if self.league_id == "404":
        raise LookupError("League not found - check the League ID and season")
    return League(self.league_id, self.year, f"League {self.league_id}", {1: "A", 2: "Mine", 3: "C", 4: "D"},
                  [1, 2, 3, 4], dict(DEFAULT_SLOTS), dict(DEFAULT_SCORING), 13, my_team_id=2)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(main, "players", lambda year: PLAYERS)
    monkeypatch.setattr(EspnClient, "league", fake_league)
    monkeypatch.setattr(EspnClient, "draft_picks", lambda self: [{"pick": 1, "round": 1, "team_id": 1, "player_id": 5}])
    main.SESSIONS.clear()
    return TestClient(main.app)


def new_user():
    email, pw = f"t-{uuid.uuid4().hex[:10]}@example.com", "pw-" + uuid.uuid4().hex
    uid = db.client().auth.admin.create_user({"email": email, "password": pw, "email_confirm": True}).user.id
    anon = create_client(config.SUPABASE_URL, _anon_key())
    token = anon.auth.sign_in_with_password({"email": email, "password": pw}).session.access_token
    return {"id": uid, "email": email, "token": token, "h": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def alice():
    u = new_user()
    yield u
    db.client().auth.admin.delete_user(u["id"])


@pytest.fixture
def bob():
    u = new_user()
    yield u
    db.client().auth.admin.delete_user(u["id"])


def test_me_and_profile_created_on_signup(api, alice):
    me = api.get("/api/me", headers=alice["h"]).json()
    assert me["signed_in"] and me["email"] == alice["email"]
    assert me["profile"]["display_name"] == alice["email"].split("@")[0] and me["profile"]["has_anthropic_key"] is False


def test_anthropic_key_stored_encrypted_and_never_returned(api, alice, monkeypatch):
    r = api.put("/api/profile", json={"anthropic_key": "sk-ant-secret-123", "birthdate": "1990-05-01"}, headers=alice["h"]).json()
    assert r["has_anthropic_key"] is True and "sk-ant" not in str(r) and r["birthdate"] == "1990-05-01"
    raw = db.client().table("user_secrets").select("anthropic_key_enc").eq("user_id", alice["id"]).execute().data[0]
    assert "sk-ant" not in raw["anthropic_key_enc"]

    seen = {}
    monkeypatch.setattr(main, "advise", lambda ctx, api_key=None, model=None, allow_server_key=False: seen.update(key=api_key) or {"source": "engine"})
    api.post("/api/sim/start", json={}, headers=alice["h"])
    api.post("/api/advice", headers=alice["h"])
    assert seen["key"] == "sk-ant-secret-123"

    assert api.put("/api/profile", json={"anthropic_key": ""}, headers=alice["h"]).json()["has_anthropic_key"] is False


def test_leagues_crud_secrets_and_isolation(api, alice, bob):
    body = {"espn_league_id": "777", "season": 2027, "espn_s2": "S2VALUE", "swid": "{SWID}", "team_id": 2}
    lg = api.post("/api/leagues", json=body, headers=alice["h"]).json()
    assert lg["has_cookies"] and lg["team_name"] == "Mine" and lg["is_default"] and "S2VALUE" not in str(lg)
    assert ESPN_CALLS[-1] == ("777", "S2VALUE", "{SWID}")
    sec = db.client().table("league_secrets").select("*").eq("league_id", lg["id"]).execute().data[0]
    assert "S2VALUE" not in sec["espn_s2_enc"] and "{SWID}" not in sec["swid_enc"]

    # edit team without re-entering cookies: stored cookies are reused for validation
    body2 = {"espn_league_id": "777", "season": 2027, "team_id": 3, "keep_cookies": True}
    assert api.post("/api/leagues", json=body2, headers=alice["h"]).json()["team_id"] == 3
    assert ESPN_CALLS[-1] == ("777", "S2VALUE", "{SWID}")

    assert api.get("/api/leagues", headers=bob["h"]).json() == []
    assert api.delete(f"/api/leagues/{lg['id']}", headers=bob["h"]).status_code == 404
    assert api.post("/api/espn/connect", json={"league_id": lg["id"]}, headers=bob["h"]).status_code == 404

    assert api.post("/api/leagues", json={**body, "team_id": 99}, headers=alice["h"]).status_code == 400
    assert api.post("/api/leagues", json={**body, "espn_league_id": "404"}, headers=alice["h"]).status_code == 404
    assert api.delete(f"/api/leagues/{lg['id']}", headers=alice["h"]).json() == []


def test_espn_draft_persists_across_restart(api, alice):
    lg = api.post("/api/leagues", json={"espn_league_id": "888", "season": 2027, "espn_s2": "a", "swid": "{b}", "team_id": 2},
                  headers=alice["h"]).json()
    st = api.post("/api/espn/connect", json={"league_id": lg["id"], "poll": False}, headers=alice["h"]).json()
    assert st["source"] == "espn" and st["current_pick"] == 2 and st["saved"]
    main.SESSIONS.clear()  # simulate server restart
    st2 = api.get("/api/state", headers=alice["h"]).json()
    assert st2["source"] == "espn" and [p["player_id"] for p in st2["picks"]] == [5] and st2["my_team_id"] == 2


def test_concurrent_requests_after_restart_all_see_draft(api, alice):
    from concurrent.futures import ThreadPoolExecutor
    r = api.post("/api/sim/start", json={}, headers=alice["h"])
    assert r.status_code == 200 and r.json()["saved"], r.text
    main.SESSIONS.clear()
    with ThreadPoolExecutor(6) as ex:
        codes = list(ex.map(lambda _: api.get("/api/state", headers=alice["h"]).status_code, range(6)))
    assert codes == [200] * 6


def test_mock_draft_persists_and_history(api, alice):
    s = api.post("/api/sim/start", json={"teams": 8, "my_slot": 3}, headers=alice["h"]).json()
    pid = api.get("/api/recommend", headers=alice["h"]).json()["recommendations"][0]["player_id"]
    s = api.post("/api/pick", json={"player_id": pid}, headers=alice["h"]).json()
    main.SESSIONS.clear()
    s2 = api.get("/api/state", headers=alice["h"]).json()
    assert s2["current_pick"] == s["current_pick"] and pid in [p["player_id"] for p in s2["picks"]]
    hist = api.get("/api/drafts", headers=alice["h"]).json()
    assert hist[0]["source"] == "sim" and hist[0]["status"] == "active" and hist[0]["picks_made"] == len(s["picks"])
    api.post("/api/sim/start", json={}, headers=alice["h"])
    statuses = [d["status"] for d in api.get("/api/drafts", headers=alice["h"]).json()]
    assert statuses.count("active") == 1 and "abandoned" in statuses


def test_guest_limits(api):
    g = {"X-Session-Id": "guest-abcdefgh"}
    assert api.post("/api/sim/start", json={}, headers=g).status_code == 200
    assert api.get("/api/state", headers=g).json()["saved"] is False
    assert api.get("/api/leagues", headers=g).status_code == 401
    assert api.post("/api/espn/connect", json={"league_id": str(uuid.uuid4())}, headers=g).status_code == 401
    assert api.get("/api/me", headers=g).json()["signed_in"] is False


def test_rls_blocks_direct_client_access(api, alice, bob):
    """Even with a valid user JWT and the public anon key, clients can't read secrets or others' rows."""
    api.put("/api/profile", json={"anthropic_key": "sk-ant-x"}, headers=alice["h"])
    api.post("/api/leagues", json={"espn_league_id": "999", "season": 2027, "espn_s2": "s", "swid": "{w}", "team_id": 2},
             headers=alice["h"])
    rest = f"{config.SUPABASE_URL}/rest/v1"

    def get(table, user):
        return requests.get(f"{rest}/{table}?select=*", headers={"apikey": _anon_key(), "Authorization": f"Bearer {user['token']}"})

    assert len(get("leagues", alice).json()) == 1          # own rows visible
    assert get("leagues", bob).json() == []                 # other user's rows hidden
    assert get("drafts", bob).json() == []
    for t in ("league_secrets", "user_secrets"):           # secrets never readable by clients
        r = get(t, alice)
        assert r.status_code in (401, 403) or r.json() == [], (t, r.status_code, r.text)


def test_no_secret_in_any_api_response(api, alice):
    """Save a key + ESPN cookies, then hit every endpoint: neither plaintext nor ciphertext may appear."""
    key, s2, swid = "sk-ant-NEVER-LEAK-123", "S2-NEVER-LEAK-456", "{SWID-NEVER-LEAK}"
    api.put("/api/profile", json={"anthropic_key": key}, headers=alice["h"])
    lg = api.post("/api/leagues", json={"espn_league_id": "321", "season": 2027, "espn_s2": s2, "swid": swid, "team_id": 2},
                  headers=alice["h"]).json()
    enc = [db.client().table("user_secrets").select("*").eq("user_id", alice["id"]).execute().data[0]["anthropic_key_enc"]]
    sec = db.client().table("league_secrets").select("*").eq("league_id", lg["id"]).execute().data[0]
    enc += [sec["espn_s2_enc"], sec["swid_enc"]]

    bodies = [
        api.get("/api/me", headers=alice["h"]), api.get("/api/profile", headers=alice["h"]),
        api.put("/api/profile", json={"display_name": "x"}, headers=alice["h"]),
        api.get("/api/leagues", headers=alice["h"]), api.get("/api/drafts", headers=alice["h"]),
        api.post("/api/espn/connect", json={"league_id": lg["id"], "poll": False}, headers=alice["h"]),
        api.get("/api/state", headers=alice["h"]), api.get("/api/recommend", headers=alice["h"]),
        api.get("/api/players", headers=alice["h"]), api.post("/api/espn/sync", headers=alice["h"]),
        api.post(f"/api/leagues/{lg['id']}/default", headers=alice["h"]), api.get("/api/health"),
    ]
    for r in bodies:
        assert r.status_code == 200, (r.request.url, r.text[:200])
        for secret in [key, s2, swid, *enc]:
            assert secret not in r.text, (r.request.url, "leaked")


def test_inseason_endpoints_and_briefing_script(api, alice, monkeypatch):
    from app import inseason
    from tests.test_inseason import _season, roster_and_schedule
    roster, sched = roster_and_schedule()
    loads = []
    monkeypatch.setattr(inseason, "load_season", lambda client, team_id, now=None: loads.append(client) or _season(roster, sched))

    assert api.get("/api/lineup", headers={"X-Session-Id": "guest-inseason"}).status_code == 401
    assert api.get("/api/lineup", headers=alice["h"]).status_code == 400  # no league saved yet

    s2, swid = "S2-INSEASON-SECRET", "{SWID-INSEASON}"
    api.post("/api/leagues", json={"espn_league_id": "555", "season": 2027, "espn_s2": s2, "swid": swid, "team_id": 2},
             headers=alice["h"])
    for path in ("/api/lineup", "/api/lineup?date=2026-10-21", "/api/week", "/api/matchup", "/api/streams", "/api/briefing"):
        r = api.get(path, headers=alice["h"])
        assert r.status_code == 200, (path, r.text[:200])
        assert s2 not in r.text and swid not in r.text
    assert len(loads) == 1  # cached across the page's calls
    assert loads[0].session.cookies.get("espn_s2") == s2 and loads[0].swid == swid  # decrypted for ESPN only
    lu = api.get("/api/lineup", headers=alice["h"]).json()
    assert lu["starters_with_games"] == 10 and lu["date"] == "2026-10-20"
    assert api.get("/api/lineup?date=bad", headers=alice["h"]).status_code == 400

    # Mac briefing script, end to end (no notification, no Claude key saved)
    import importlib.util
    spec = importlib.util.spec_from_file_location("daily_briefing", Path(__file__).resolve().parents[1] / "scripts" / "daily_briefing.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = mod.run(alice["id"])
    assert not b["idle"] and b["summary"] is None and "lineup move" in b["headline"]
    saved = api.get("/api/briefing", headers=alice["h"]).json()
    assert saved["headline"] == b["headline"]
    for f in (main.config.CACHE_DIR / "briefings").glob(f"{alice['id']}_*.json"):
        assert s2 not in f.read_text()
        f.unlink()
