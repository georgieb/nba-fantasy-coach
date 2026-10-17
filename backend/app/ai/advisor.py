"""Claude explains / tiebreaks the engine's ranking. The engine stays authoritative:
any failure falls back to the engine's #1 pick."""
import json

import anthropic

from app.config import CLAUDE_MODEL

SYSTEM = """You are a fantasy basketball draft advisor for an ESPN head-to-head POINTS league \
(snake draft). You receive the deterministic engine's top candidates with their projected \
fantasy points (fpts), VORP (value over replacement at their position), VONA (value lost by \
waiting until our following pick), need (share of value that lands in our starting lineup), \
survival odds to our next pick, and our open lineup slots. Choose ONE player from the \
candidates. Prefer the engine's #1 unless there is a concrete reason (injury risk, tier cliff \
at a scarce slot, roster balance, a candidate very likely to come back next round). Be \
decisive and specific; cite the numbers. Keep the rationale to 2-3 sentences."""

SCHEMA = {
    "type": "object",
    "properties": {
        "player_id": {"type": "integer"},
        "player_name": {"type": "string"},
        "rationale": {"type": "string"},
        "wait_on": {"type": "string", "description": "A player likely to still be there at our next pick, or empty"},
        "strategy_note": {"type": "string", "description": "One line on roster build direction for the next 2 rounds"},
    },
    "required": ["player_id", "player_name", "rationale", "wait_on", "strategy_note"],
    "additionalProperties": False,
}

_clients: dict[str | None, anthropic.Anthropic] = {}


def _get_client(api_key: str | None) -> anthropic.Anthropic:
    """One client per user key; None falls back to the server's own credentials (env)."""
    if api_key not in _clients:
        _clients[api_key] = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=1) if api_key \
            else anthropic.Anthropic(timeout=30.0, max_retries=1)
    return _clients[api_key]


def advise(context: dict, api_key: str | None = None, model: str | None = None, allow_server_key: bool = False) -> dict:
    recs = context["candidates"]
    fallback = {"player_id": recs[0]["player_id"], "player_name": recs[0]["name"], "source": "engine",
                "rationale": "Engine top pick (Claude unavailable).", "wait_on": "", "strategy_note": ""} if recs else {}
    if not api_key and not allow_server_key:
        return {**fallback, "error": "No Anthropic API key - sign in and add yours on the Profile page"}
    try:
        resp = _get_client(api_key).beta.messages.create(
            model=model or CLAUDE_MODEL,
            max_tokens=2000,
            system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(context, separators=(",", ":"))}],
        )
    except anthropic.AuthenticationError:
        return {**fallback, "error": "Anthropic API key was rejected - check it on the Profile page"}
    except anthropic.RateLimitError:
        return {**fallback, "error": "Claude rate limited"}
    except anthropic.APIStatusError as e:
        return {**fallback, "error": f"Claude API error {e.status_code}: {e.message}"}
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        return {**fallback, "error": "Could not reach Claude"}
    except anthropic.AnthropicError as e:
        return {**fallback, "error": str(e)}
    except TypeError:  # SDK raises this when no credentials are configured
        return {**fallback, "error": "No Anthropic API key - sign in and add yours on the Profile page"}

    if resp.stop_reason != "end_turn":
        return {**fallback, "error": f"Claude stopped: {resp.stop_reason}"}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        return {**fallback, "error": "Unparseable Claude response"}
    valid_ids = {r["player_id"] for r in recs}
    if out.get("player_id") not in valid_ids:
        return {**fallback, "error": "Claude picked a player outside the candidate list"}
    return {**out, "source": "claude"}


BRIEFING_SYSTEM = """You write a fantasy basketball manager's daily briefing for an ESPN head-to-head POINTS \
league. You get today's recommended lineup moves, injury flags, streaming suggestions and the weekly \
matchup projection as JSON. Write 3-5 short plain-text lines: what to change in ESPN before tip-off, \
which injuries to re-check, whether a stream is worth it, and how the matchup looks. No markdown, \
no preamble. Never invent players, stats or news that aren't in the JSON."""


def summarize_briefing(b: dict, api_key: str | None, model: str | None = None) -> str | None:
    """Short Claude summary of a briefing, or None if no key / any failure (the briefing works without it)."""
    if not api_key:
        return None
    try:
        resp = _get_client(api_key).beta.messages.create(
            model=model or CLAUDE_MODEL,
            max_tokens=1500,
            system=BRIEFING_SYSTEM,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": json.dumps(b, separators=(",", ":"), default=str)}],
        )
    except (anthropic.AnthropicError, TypeError):
        return None
    if resp.stop_reason != "end_turn":
        return None
    return next((blk.text for blk in resp.content if blk.type == "text"), None)
