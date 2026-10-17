"""API. Signed-in users (Supabase JWT) get saved leagues, encrypted secrets and persisted drafts;
guests (X-Session-Id) can run mock drafts in memory only."""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import config, db, inseason
from app.ai.advisor import advise
from app.auth import Identity, identity, require_user
from app.data.espn_client import EspnClient, Player
from app.draft_state import DraftState
from app.engine import esoteric
from app.engine.roster_fit import best_lineup, starter_slots
from app.engine.valuation import recommend
from app.sim.mock import advance_to_me, new_sim

app = FastAPI(title="NBA Fantasy Draft Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])

SESSION_TTL = 24 * 3600
MAX_GUEST_SESSIONS = 500
IDLE_PAUSE = 300    # stop hitting ESPN when the page hasn't been open for 5 min; resumes on next visit
MAX_BACKOFF = 30    # seconds between ESPN retries after repeated errors
log = logging.getLogger("draft")


@dataclass
class Session:
    ident: Identity
    client: EspnClient | None = None
    state: DraftState | None = None
    polling: bool = False
    draft_id: str | None = None
    profile: dict | None = None
    last_seen: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    ready: threading.Event = field(default_factory=threading.Event)  # set once rehydration finishes
    sync_failures: int = 0
    season: object | None = None   # cached inseason.Season for the lineup pages
    season_at: float = 0.0
    next_sync_at: float = 0.0  # backoff: don't call ESPN again before this time


SESSIONS: dict[str, Session] = {}
_sessions_lock = threading.Lock()
_players_by_year: dict[int, list[Player]] = {}


def players(year: int) -> list[Player]:
    """Projections come from ESPN's public endpoint - shared across users."""
    if year not in _players_by_year:
        _players_by_year[year] = EspnClient(year).players()
    return _players_by_year[year]


def require_accounts():
    if not config.ACCOUNTS_ENABLED:
        raise HTTPException(503, "Accounts are not configured on this server")


# ---------------- sessions ----------------
def session(ident: Identity = Depends(identity)) -> Session:
    with _sessions_lock:
        s = SESSIONS.get(ident.key)
        fresh = s is None
        if fresh:
            if not ident.is_user:
                guests = sorted((v.last_seen, k) for k, v in SESSIONS.items() if k.startswith("guest:"))
                for _, k in guests[:max(0, len(guests) - MAX_GUEST_SESSIONS + 1)]:
                    del SESSIONS[k]  # evict least-recently-used guests
            s = SESSIONS[ident.key] = Session(ident)
        s.last_seen = time.time()
    if fresh:
        try:
            if ident.is_user and config.ACCOUNTS_ENABLED:
                with s.lock:
                    _rehydrate(s)
        finally:
            s.ready.set()
    else:
        s.ready.wait(timeout=30)  # concurrent first requests wait for the draft to be restored
    return s


def _rehydrate(s: Session):
    """After a server restart, rebuild a signed-in user's active draft from the database."""
    try:
        d = db.active_draft(s.ident.user_id)
        if not d:
            return
        cfg = d["config"] or {}
        if d["source"] == "sim":
            st = new_sim(players(config.ESPN_YEAR), cfg.get("teams", 10), cfg.get("my_slot", 1))
        else:
            client, league_row = _league_client(s.ident.user_id, d["league_id"])
            st = DraftState(client.league(), players(client.year), league_row["team_id"], "espn")
            s.client, s.polling = client, True
        st.replace_picks(d["picks"])
        s.state, s.draft_id = st, d["id"]
    except Exception:
        log.exception("Could not restore draft for %s", s.ident.key)
        s.state, s.draft_id = None, None  # start clean rather than fail every request


def persist(s: Session):
    if s.ident.is_user and s.draft_id and s.state and config.ACCOUNTS_ENABLED:
        db.save_draft(s.ident.user_id, s.draft_id, s.state.picks, s.state.complete)


def state(s: Session) -> DraftState:
    if s.state is None:
        raise HTTPException(400, "No draft session - start a mock draft or connect to ESPN")
    return s.state


def profile(s: Session) -> dict:
    if not s.ident.is_user or not config.ACCOUNTS_ENABLED:
        return {}
    if s.profile is None:
        s.profile = db.get_profile(s.ident.user_id)
    return s.profile


# ---------------- ESPN ----------------
def _league_client(uid: str, league_id: str) -> tuple[EspnClient, dict]:
    row = db.get_league(uid, league_id)
    if not row:
        raise HTTPException(404, "League not found")
    s2, swid = db.league_cookies(uid, league_id)
    return EspnClient(row["season"], row["espn_league_id"], s2, swid), row


def _espn_errors(fn):
    try:
        return fn()
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"ESPN request failed: {e}")


def _backoff(failures: int) -> float:
    return min(MAX_BACKOFF, config.POLL_SECONDS * 2 ** failures)


def _sync(s: Session):
    st = s.state
    if st is None or st.source != "espn" or s.client is None:
        return
    try:
        picks = s.client.draft_picks()
        if [p["player_id"] for p in picks] != [p["player_id"] for p in st.picks]:
            st.replace_picks(picks)
            persist(s)
        st.last_sync_error = None
        s.sync_failures, s.next_sync_at = 0, 0.0
    except Exception as e:  # keep polling, but back off; surface error in UI
        s.sync_failures += 1
        wait = _backoff(s.sync_failures)
        s.next_sync_at = time.time() + wait
        st.last_sync_error = f"{e} (retrying in {wait:.0f}s)"
    if st.complete:
        s.polling = False  # draft over: stop calling ESPN


def poll_tick(now: float):
    """One poller pass: expire old sessions, then sync live drafts that are open, unfinished and not backing off."""
    with _sessions_lock:
        for k in [k for k, v in SESSIONS.items() if now - v.last_seen > SESSION_TTL]:
            del SESSIONS[k]
        active = [v for v in SESSIONS.values() if v.polling]
    for s in active:
        if now - s.last_seen > IDLE_PAUSE:  # tab closed / laptop asleep: pause until the page polls again
            continue
        if now < s.next_sync_at:
            continue
        _sync(s)


def _poller():
    while True:
        poll_tick(time.time())
        time.sleep(config.POLL_SECONDS)


def _prefetch():
    try:
        pool = sorted(players(config.ESPN_YEAR), key=lambda p: p.rank or 999)[:300]
        esoteric.birthdates([p.id for p in pool])
    except Exception:
        pass  # astrology mode retries on demand


threading.Thread(target=_poller, daemon=True).start()
threading.Thread(target=_prefetch, daemon=True).start()


class LeagueCreds(BaseModel):
    espn_league_id: str = Field(min_length=1, max_length=20, pattern=r"^\d+$")
    season: int = config.ESPN_YEAR
    espn_s2: str | None = None
    swid: str | None = None


def _clean(v: str | None) -> str | None:
    return (v or "").strip() or None


@app.post("/api/league/lookup")
def league_lookup(creds: LeagueCreds, _: Identity = Depends(require_user)):
    """Validate credentials and list teams so the user can pick theirs (nothing is saved)."""
    client = EspnClient(creds.season, creds.espn_league_id, _clean(creds.espn_s2), _clean(creds.swid))
    lg = _espn_errors(client.league)
    return {"name": lg.name, "teams": [{"id": t, "name": n} for t, n in lg.teams.items()],
            "my_team_id": lg.my_team_id, "slots": lg.slots, "rounds": lg.rounds}


# ---------------- account: profile + leagues + drafts ----------------
@app.get("/api/me")
def me(s: Session = Depends(session)):
    return {"signed_in": s.ident.is_user, "email": s.ident.email, "accounts_enabled": config.ACCOUNTS_ENABLED,
            "profile": profile(s) or None}


class ProfileReq(BaseModel):
    display_name: str | None = Field(None, max_length=60)
    birthdate: date | None = None
    claude_model: str | None = Field(None, max_length=60)
    anthropic_key: str | None = Field(None, max_length=300)  # "" clears it; omitted leaves it unchanged


@app.get("/api/profile")
def get_profile(ident: Identity = Depends(require_user), s: Session = Depends(session)):
    require_accounts()
    s.profile = db.get_profile(ident.user_id)
    return s.profile


@app.put("/api/profile")
def put_profile(req: ProfileReq, ident: Identity = Depends(require_user), s: Session = Depends(session)):
    require_accounts()
    fields = req.model_dump(exclude_unset=True)
    if "anthropic_key" in fields:
        db.set_anthropic_key(ident.user_id, _clean(fields.pop("anthropic_key")))
    if fields.get("birthdate"):
        fields["birthdate"] = fields["birthdate"].isoformat()
    s.profile = db.update_profile(ident.user_id, fields)
    return s.profile


@app.get("/api/leagues")
def get_leagues(ident: Identity = Depends(require_user)):
    require_accounts()
    return db.list_leagues(ident.user_id)


class SaveLeagueReq(LeagueCreds):
    team_id: int
    keep_cookies: bool = False  # editing a saved league without re-entering cookies


@app.post("/api/leagues")
def post_league(req: SaveLeagueReq, ident: Identity = Depends(require_user)):
    require_accounts()
    s2, swid = _clean(req.espn_s2), _clean(req.swid)
    if req.keep_cookies:
        existing = next((l for l in db.list_leagues(ident.user_id)
                         if l["espn_league_id"] == req.espn_league_id and l["season"] == req.season), None)
        if existing:
            s2, swid = db.league_cookies(ident.user_id, existing["id"])
    lg = _espn_errors(EspnClient(req.season, req.espn_league_id, s2, swid).league)
    if req.team_id not in lg.teams:
        raise HTTPException(400, "That team isn't in this league")
    return db.save_league(ident.user_id, req.espn_league_id, req.season, lg.name, req.team_id,
                          lg.teams[req.team_id], s2, swid, keep_cookies=req.keep_cookies)


@app.post("/api/leagues/{league_id}/default")
def make_default(league_id: str, ident: Identity = Depends(require_user)):
    require_accounts()
    if not db.get_league(ident.user_id, league_id):
        raise HTTPException(404, "League not found")
    db.set_default_league(ident.user_id, league_id)
    return db.list_leagues(ident.user_id)


@app.delete("/api/leagues/{league_id}")
def remove_league(league_id: str, ident: Identity = Depends(require_user)):
    require_accounts()
    if not db.delete_league(ident.user_id, league_id):
        raise HTTPException(404, "League not found")
    return db.list_leagues(ident.user_id)


@app.get("/api/drafts")
def drafts(ident: Identity = Depends(require_user)):
    require_accounts()
    rows = db.list_drafts(ident.user_id)
    return [{**{k: v for k, v in r.items() if k != "picks"}, "picks_made": len(r["picks"])} for r in rows]


# ---------------- live ESPN draft ----------------
class ConnectReq(BaseModel):
    league_id: str  # saved league's uuid
    poll: bool = True


@app.post("/api/espn/connect")
def espn_connect(req: ConnectReq, ident: Identity = Depends(require_user), s: Session = Depends(session)):
    require_accounts()
    client, row = _league_client(ident.user_id, req.league_id)
    league = _espn_errors(client.league)
    if row["team_id"] not in league.teams:
        raise HTTPException(400, "Your saved team is no longer in this league - edit it on the Profile page")
    with s.lock:
        st = DraftState(league, players(client.year), row["team_id"], "espn")
        prev = db.latest_espn_draft(ident.user_id, row["id"])
        if prev:
            st.replace_picks(prev["picks"])
            db.reactivate_draft(ident.user_id, prev["id"])
            s.draft_id = prev["id"]
        else:
            s.draft_id = db.create_draft(ident.user_id, "espn", {"espn_league_id": row["espn_league_id"]}, row["id"])
        s.client, s.state, s.polling = client, st, req.poll
    _sync(s)
    persist(s)
    return summary(s)


class PollReq(BaseModel):
    enabled: bool


@app.post("/api/espn/polling")
def espn_polling(req: PollReq, s: Session = Depends(session)):
    s.polling = req.enabled and s.state is not None and s.state.source == "espn" and not s.state.complete
    s.sync_failures, s.next_sync_at = 0, 0.0
    return summary(s)


@app.post("/api/espn/sync")
def espn_sync(s: Session = Depends(session)):
    _sync(s)
    return summary(s)


# ---------------- Mock draft ----------------
class SimReq(BaseModel):
    teams: int = Field(10, ge=4, le=16)
    my_slot: int = 1
    seed: int | None = None


@app.post("/api/sim/start")
def sim_start(req: SimReq, s: Session = Depends(session)):
    if not 1 <= req.my_slot <= req.teams:
        raise HTTPException(400, "my_slot must be between 1 and teams")
    with s.lock:
        s.polling, s.client = False, None
        s.state = new_sim(players(config.ESPN_YEAR), req.teams, req.my_slot, req.seed)
        advance_to_me(s.state)
        s.draft_id = None
        if s.ident.is_user and config.ACCOUNTS_ENABLED:
            s.draft_id = db.create_draft(s.ident.user_id, "sim", {"teams": req.teams, "my_slot": req.my_slot},
                                         picks=s.state.picks)
    return summary(s)


# ---------------- Picks ----------------
class PickReq(BaseModel):
    player_id: int


@app.post("/api/pick")
def make_pick(req: PickReq, s: Session = Depends(session)):
    st = state(s)
    try:
        st.add_pick(req.player_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if st.source == "sim":
        advance_to_me(st)
    persist(s)
    return summary(s)


@app.post("/api/undo")
def undo(s: Session = Depends(session)):
    st = state(s)
    st.undo()
    if st.source == "sim":  # rewind to our previous turn
        while st.picks and st.on_clock != st.my_team_id:
            st.undo()
    persist(s)
    return summary(s)


# ---------------- Read models ----------------
def _player_row(st: DraftState, pid: int) -> dict:
    p = st.players.get(pid)
    if not p:
        return {"player_id": pid, "name": f"Player {pid}", "positions": [], "fpts": 0, "team": ""}
    return {"player_id": pid, "name": p.name, "team": p.team, "positions": p.positions, "fpts": st.fpts[pid],
            "fpg": round(st.fpts[pid] / p.gp, 1) if p.gp else 0, "adp": p.adp, "injury": p.injury}


@app.get("/api/state")
def summary(s: Session = Depends(session)):
    st = state(s)
    slots = starter_slots(st.league.slots)
    rosters = {}
    for t, ids in st.rosters().items():
        total, assign = best_lineup([(i, st.players[i].positions, st.fpts[i]) for i in ids], slots)
        rosters[t] = {"name": st.league.teams.get(t, str(t)), "projected_starters_fpts": round(total, 1),
                      "players": [{**_player_row(st, i), "slot": assign.get(i, "BE")} for i in ids]}
    return {
        "source": st.source, "league": st.league.name, "teams": st.league.teams, "pick_order": st.league.pick_order,
        "rounds": st.league.rounds, "slots": st.league.slots, "my_team_id": st.my_team_id,
        "current_pick": st.current_pick, "on_clock": st.on_clock, "complete": st.complete,
        "picks": [{**p, **_player_row(st, p["player_id"])} for p in st.picks],
        "rosters": rosters, "polling": s.polling, "sync_error": st.last_sync_error, "saved": bool(s.draft_id),
    }


def _user_birthdate(s: Session, override: str | None) -> date | None:
    raw = override or profile(s).get("birthdate")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(400, "Birthdate must be YYYY-MM-DD")


@app.get("/api/recommend")
def get_recommend(astro: bool = False, weight: float = 0.03, birthdate: str | None = Query(None, max_length=10),
                  top: int = Query(12, ge=1, le=25),
                  s: Session = Depends(session)):
    st = state(s)
    if st.complete:
        return {"complete": True, "recommendations": [], "build_plan": []}
    user_dob = _user_birthdate(s, birthdate) if astro else None
    with st.lock:
        details = {}
        if astro:
            pool = sorted(st.available(), key=lambda i: -st.fpts[i])[:150]
            dobs = esoteric.birthdates(pool)
            details = {i: esoteric.compatibility(date.fromisoformat(d), user_dob) for i, d in dobs.items()}
        res = recommend(st, top=top, astro_scores={i: d["score"] for i, d in details.items()},
                        astro_weight=max(0.0, min(weight, 0.10)) if astro else 0.0)
    for r in res["recommendations"]:
        if r["player_id"] in details:
            r["astro_detail"] = details[r["player_id"]]
    return res


@app.post("/api/advice")
def get_advice(astro: bool = False, weight: float = 0.03, birthdate: str | None = Query(None, max_length=10),
               x_anthropic_key: str | None = Header(None), x_claude_model: str | None = Header(None),
               s: Session = Depends(session)):
    st = state(s)
    rec = get_recommend(astro, weight, birthdate, top=8, s=s)
    if not rec.get("recommendations"):
        raise HTTPException(400, "No candidates")
    my = summary(s)["rosters"].get(st.my_team_id, {})
    ctx = {
        "league": {"teams": len(st.league.pick_order), "scoring": "H2H points", "starting_slots": st.league.slots},
        "current_pick": rec["current_pick"], "my_next_pick": rec["my_next_pick"], "my_following_pick": rec["my_following_pick"],
        "my_roster": [{k: p[k] for k in ("name", "positions", "fpts", "slot")} for p in my.get("players", [])],
        "my_open_slots": rec["my_open_slots"], "build_plan": rec["build_plan"][:5],
        "candidates": [{k: r[k] for k in ("player_id", "name", "positions", "fpts", "fpg", "gp", "adp", "injury",
                                           "slot", "need", "vorp", "vona", "survive_next", "score") if k in r}
                       | ({"astro": r["astro_detail"]} if "astro_detail" in r else {})
                       for r in rec["recommendations"]],
    }
    key = _clean(x_anthropic_key)
    if s.ident.is_user and config.ACCOUNTS_ENABLED:
        key = db.get_anthropic_key(s.ident.user_id) or key
    model = _clean(x_claude_model) or profile(s).get("claude_model")
    return advise(ctx, api_key=key, model=model, allow_server_key=config.SHARE_SERVER_KEY)


@app.get("/api/players")
def board(pos: str | None = Query(None, pattern="^(PG|SG|SF|PF|C)$"), q: str | None = Query(None, max_length=50),
          limit: int = Query(200, ge=1, le=300), s: Session = Depends(session)):
    st = state(s)
    rows = []
    for i in sorted(st.available(), key=lambda i: -st.fpts[i]):
        p = st.players[i]
        if pos and pos not in p.positions:
            continue
        if q and q.lower() not in p.name.lower():
            continue
        rows.append(_player_row(st, i))
        if len(rows) >= limit:
            break
    return rows


# ---------------- in-season (recommend only: nothing is ever written to ESPN) ----------------
SEASON_CACHE_SECONDS = 60


def _season(s: Session, refresh: bool = False) -> "inseason.Season":
    require_accounts()
    if not s.ident.is_user:
        raise HTTPException(401, "Sign in to use this feature")
    if refresh or s.season is None or time.time() - s.season_at > SEASON_CACHE_SECONDS:
        leagues = db.list_leagues(s.ident.user_id)
        row = next((l for l in leagues if l["is_default"]), leagues[0] if leagues else None)
        if not row:
            raise HTTPException(400, "Add your ESPN league on the Profile page first")
        client, row = _league_client(s.ident.user_id, row["id"])
        s.season = _espn_errors(lambda: inseason.load_season(client, row["team_id"]))
        s.season_at = time.time()
    return s.season


@app.get("/api/lineup")
def lineup(date_: str | None = Query(None, alias="date", max_length=10), refresh: bool = False,
           s: Session = Depends(session)):
    se = _season(s, refresh)
    sp = None
    if date_:
        try:
            day = date.fromisoformat(date_)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
        sp = inseason.scoring_period_for(day, inseason.season_start(se.schedule))
    return inseason.lineup_view(se, sp)


@app.get("/api/week")
def week(s: Session = Depends(session)):
    return inseason.week_view(_season(s))


@app.get("/api/matchup")
def matchup(s: Session = Depends(session)):
    return inseason.matchup_view(_season(s))


@app.get("/api/streams")
def streams(s: Session = Depends(session)):
    se = _season(s)
    return _espn_errors(lambda: inseason.streams_view(se))


@app.get("/api/briefing")
def latest_briefing(s: Session = Depends(session)):
    """Most recent briefing written by the Mac job (scripts/daily_briefing.py), if any."""
    require_accounts()
    if not s.ident.is_user:
        raise HTTPException(401, "Sign in to use this feature")
    files = sorted((config.CACHE_DIR / "briefings").glob(f"{s.ident.user_id}_*.json"))
    return json.loads(files[-1].read_text()) if files else None


@app.get("/api/health")
def health():
    return {"ok": True, "default_year": config.ESPN_YEAR, "default_model": config.CLAUDE_MODEL,
            "server_key_shared": config.SHARE_SERVER_KEY, "accounts_enabled": config.ACCOUNTS_ENABLED}
