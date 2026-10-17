"""Daily fantasy briefing for your Mac (run by launchd at noon and 6 PM; see scripts/install_briefing.sh).

Reads your saved league from Supabase, computes today's lineup moves, injury flags, streams and the
matchup projection (read-only against ESPN), saves it for the lineup page and shows a notification.

    backend/.venv/bin/python backend/scripts/daily_briefing.py [--email you@example.com] [--no-notify]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db, inseason  # noqa: E402
from app.ai.advisor import summarize_briefing  # noqa: E402
from app.data.espn_client import EspnClient  # noqa: E402

OUT_DIR = config.CACHE_DIR / "briefings"
KEEP = 14


def notify(title: str, message: str):
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)} sound name \"Glass\""
    subprocess.run(["osascript", "-e", script], check=False, timeout=10)


def find_user(email: str | None) -> tuple[str, str]:
    users = db.client().auth.admin.list_users()
    if email:
        users = [u for u in users if (u.email or "").lower() == email.lower()]
    if len(users) != 1:
        raise SystemExit(f"Expected exactly one matching user, found {len(users)} - pass --email")
    return users[0].id, users[0].email


def run(uid: str, client_factory=EspnClient, now: float | None = None) -> dict:
    leagues = db.list_leagues(uid)
    row = next((l for l in leagues if l["is_default"]), leagues[0] if leagues else None)
    if not row:
        raise SystemExit("No saved league - add one on the Profile page")
    s2, swid = db.league_cookies(uid, row["id"])
    client = client_factory(row["season"], row["espn_league_id"], s2, swid)
    season = inseason.load_season(client, row["team_id"], now=now)
    b = inseason.briefing(season)
    profile = db.get_profile(uid)
    b["summary"] = None if b["idle"] else summarize_briefing(b, db.get_anthropic_key(uid), profile.get("claude_model"))
    b["league"] = row["name"]
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{uid}_{b['date']}.json").write_text(json.dumps(b, indent=1, default=str))
    for old in sorted(OUT_DIR.glob(f"{uid}_*.json"))[:-KEEP]:
        old.unlink()
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()
    if not config.ACCOUNTS_ENABLED:
        raise SystemExit("Supabase settings missing from .env")
    uid, email = find_user(args.email)
    try:
        b = run(uid)
    except SystemExit:
        raise
    except Exception as e:  # never leave the user guessing: surface failures as a notification too
        if not args.no_notify:
            notify("Fantasy briefing failed", str(e)[:200])
        raise
    print(json.dumps({k: b[k] for k in ("date", "headline", "moves", "flags")}, indent=1))
    if not args.no_notify:
        notify(f"🏀 {b['league'].strip()} - {b['date']}", b["headline"])


if __name__ == "__main__":
    main()
