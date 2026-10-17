import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if val not in (None, "") else default


# League credentials and API keys are entered per user in the app (Setup page), not here.
ESPN_YEAR = int(env("ESPN_YEAR", "2027"))  # default season (2026-27 = 2027)
# If true, users without their own Anthropic key use the server's ANTHROPIC_API_KEY (you pay).
SHARE_SERVER_KEY = env("SHARE_SERVER_KEY", "false").lower() == "true"
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-opus-5-5")
POLL_SECONDS = float(env("POLL_SECONDS", "3"))

# Supabase (accounts). When unset, the app runs in guest-only mode.
SUPABASE_URL = (env("SUPABASE_URL") or "").rstrip("/") or None
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_JWT_SECRET = env("SUPABASE_JWT_SECRET")  # only for HS256 (legacy / local) projects
APP_ENCRYPTION_KEY = env("APP_ENCRYPTION_KEY")    # Fernet key for ESPN cookies + Anthropic keys
ACCOUNTS_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and APP_ENCRYPTION_KEY)
