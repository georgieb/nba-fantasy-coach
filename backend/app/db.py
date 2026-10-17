"""Supabase repository. Uses the service-role key (bypasses RLS), so every query is
explicitly scoped by user_id. Secrets are encrypted before they leave this module."""
from supabase import Client, create_client

from app import config
from app.crypto import decrypt, encrypt

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


# ---------------- profile ----------------
PROFILE_FIELDS = ("display_name", "birthdate", "claude_model")


def get_profile(uid: str) -> dict:
    rows = client().table("profiles").select("*").eq("id", uid).limit(1).execute().data
    if not rows:  # user created before the trigger existed
        rows = client().table("profiles").insert({"id": uid}).execute().data
    key = client().table("user_secrets").select("anthropic_key_enc").eq("user_id", uid).limit(1).execute().data
    return {**{k: rows[0].get(k) for k in PROFILE_FIELDS}, "has_anthropic_key": bool(key and key[0]["anthropic_key_enc"])}


def update_profile(uid: str, fields: dict) -> dict:
    data = {k: v for k, v in fields.items() if k in PROFILE_FIELDS}
    if data:
        client().table("profiles").update(data).eq("id", uid).execute()
    return get_profile(uid)


def set_anthropic_key(uid: str, key: str | None):
    client().table("user_secrets").upsert({"user_id": uid, "anthropic_key_enc": encrypt(key)}).execute()


def get_anthropic_key(uid: str) -> str | None:
    rows = client().table("user_secrets").select("anthropic_key_enc").eq("user_id", uid).limit(1).execute().data
    return decrypt(rows[0]["anthropic_key_enc"]) if rows else None


# ---------------- leagues ----------------
LEAGUE_COLS = "id, espn_league_id, season, name, team_id, team_name, is_default, created_at"


def _public_league(row: dict) -> dict:
    sec = row.pop("league_secrets", None) or {}
    if isinstance(sec, list):
        sec = sec[0] if sec else {}
    row["has_cookies"] = bool(sec.get("espn_s2_enc") and sec.get("swid_enc"))
    return row


def list_leagues(uid: str) -> list[dict]:
    rows = (client().table("leagues").select(f"{LEAGUE_COLS}, league_secrets(espn_s2_enc, swid_enc)")
            .eq("user_id", uid).order("created_at").execute().data)
    return [_public_league(r) for r in rows]


def get_league(uid: str, league_id: str) -> dict | None:
    rows = (client().table("leagues").select(f"{LEAGUE_COLS}, league_secrets(espn_s2_enc, swid_enc)")
            .eq("user_id", uid).eq("id", league_id).limit(1).execute().data)
    return _public_league(rows[0]) if rows else None


def save_league(uid: str, espn_league_id: str, season: int, name: str, team_id: int, team_name: str,
                espn_s2: str | None, swid: str | None, keep_cookies: bool = False) -> dict:
    existing = client().table("leagues").select("id").eq("user_id", uid).execute().data
    row = (client().table("leagues").upsert(
        {"user_id": uid, "espn_league_id": espn_league_id, "season": season, "name": name,
         "team_id": team_id, "team_name": team_name, **({"is_default": True} if not existing else {})},
        on_conflict="user_id,espn_league_id,season").execute().data[0])
    if not keep_cookies:
        client().table("league_secrets").upsert(
            {"league_id": row["id"], "espn_s2_enc": encrypt(espn_s2), "swid_enc": encrypt(swid)}).execute()
    return get_league(uid, row["id"])


def league_cookies(uid: str, league_id: str) -> tuple[str | None, str | None]:
    if not get_league(uid, league_id):  # ownership check
        return None, None
    rows = client().table("league_secrets").select("espn_s2_enc, swid_enc").eq("league_id", league_id).limit(1).execute().data
    return (decrypt(rows[0]["espn_s2_enc"]), decrypt(rows[0]["swid_enc"])) if rows else (None, None)


def set_default_league(uid: str, league_id: str):
    client().table("leagues").update({"is_default": False}).eq("user_id", uid).neq("id", league_id).execute()
    client().table("leagues").update({"is_default": True}).eq("user_id", uid).eq("id", league_id).execute()


def delete_league(uid: str, league_id: str) -> bool:
    return bool(client().table("leagues").delete().eq("user_id", uid).eq("id", league_id).execute().data)


# ---------------- drafts ----------------
def create_draft(uid: str, source: str, config_: dict, league_id: str | None = None, picks: list | None = None) -> str:
    client().table("drafts").update({"status": "abandoned"}).eq("user_id", uid).eq("status", "active").execute()
    row = client().table("drafts").insert({"user_id": uid, "source": source, "config": config_,
                                           "league_id": league_id, "picks": picks or []}).execute().data[0]
    return row["id"]


def reactivate_draft(uid: str, draft_id: str):
    client().table("drafts").update({"status": "abandoned"}).eq("user_id", uid).eq("status", "active").neq("id", draft_id).execute()
    client().table("drafts").update({"status": "active"}).eq("user_id", uid).eq("id", draft_id).execute()


def save_draft(uid: str, draft_id: str, picks: list, complete: bool):
    (client().table("drafts").update({"picks": picks, "status": "complete" if complete else "active"})
     .eq("user_id", uid).eq("id", draft_id).execute())


def active_draft(uid: str) -> dict | None:
    rows = (client().table("drafts").select("*").eq("user_id", uid).eq("status", "active")
            .order("updated_at", desc=True).limit(1).execute().data)
    return rows[0] if rows else None


def latest_espn_draft(uid: str, league_id: str) -> dict | None:
    rows = (client().table("drafts").select("*").eq("user_id", uid).eq("league_id", league_id).eq("source", "espn")
            .neq("status", "complete").order("updated_at", desc=True).limit(1).execute().data)
    return rows[0] if rows else None


def list_drafts(uid: str, limit: int = 20) -> list[dict]:
    return (client().table("drafts").select("id, source, status, config, league_id, created_at, updated_at, picks")
            .eq("user_id", uid).order("updated_at", desc=True).limit(limit).execute().data)
