# NBA Fantasy Draft Assistant

A live draft assistant for an ESPN H2H Points snake draft. It polls ESPN for picks and ranks the best pick for your roster.

## Run it
```bash
supabase start                          # local Supabase (Docker). Applies supabase/migrations.
cp .env.example .env                    # fill from `supabase status -o env` (+ generate APP_ENCRYPTION_KEY)
cp frontend/.env.example frontend/.env.local
./run.sh                                # backend :8000 + frontend http://localhost:3000
```
Without Supabase settings the app runs **guest-only**: mock drafts only, nothing is saved.

## Accounts (Supabase)
- **Sign in** with email + password or a magic link (`/login`).
- **Profile** (`/profile`): display name, birthdate (for astrology mode), Claude model, and your Anthropic API key. The key is write-only: it's stored encrypted and never shown again.
- **ESPN leagues:** enter the league ID + season. For a private league, also add the `espn_s2` and `SWID` cookies (DevTools → Application → Cookies on espn.com). **Test connection** lists the teams; pick yours and save. You can save several leagues and set a default. ESPN has no OAuth, so the cookies are the only way in. They're stored encrypted and only the backend decrypts them, when talking to ESPN.
- **Drafts are saved:** every pick is written to your account. A server restart or a different device picks up where you left off, and past drafts show under Draft history.
- **Guests** can run mock drafts (in memory only). Settings left in localStorage by the earlier version can be imported from the Profile page.

**Security model:** the browser only ever has the public anon key and the user's own JWT. The backend checks each JWT (JWKS for ES256/RS256 projects, or `SUPABASE_JWT_SECRET` for HS256) and talks to Postgres with the service-role key, scoping every query by user. RLS limits `profiles`, `leagues` and `drafts` to their owner. `league_secrets` and `user_secrets` have RLS on with no policies and grants revoked, so no client role can read them. Secrets are encrypted with Fernet (`APP_ENCRYPTION_KEY`) before they're stored.

### Keys & local security
- `.env` holds the server-only secrets: `SUPABASE_SERVICE_ROLE_KEY` (full database access) and `APP_ENCRYPTION_KEY` (decrypts saved ESPN cookies and Anthropic keys). Never commit or share it; it's gitignored. If `APP_ENCRYPTION_KEY` is lost, users just re-enter their cookies and key.
- The browser only ever gets `NEXT_PUBLIC_SUPABASE_URL` and the public anon key. Row-level security protects the data.
- `run.sh` binds both servers to `127.0.0.1`, so the app isn't reachable from your Wi-Fi.
- Before committing: `gitleaks dir .` (scans only files git would publish if run on a clean checkout), `npm audit --omit=dev` in `frontend/`, and `backend/.venv/bin/pip-audit`.

### Going to a hosted Supabase project
1. Create a project at supabase.com, then run `supabase login` and `supabase link --project-ref <ref>`.
2. Run `supabase db push` to apply the migrations.
3. In Auth → URL Configuration, set the Site URL / redirect URLs to your app's URL.
4. Put the project URL, the service-role key and a new `APP_ENCRYPTION_KEY` in `.env`. Put the URL and anon key in `frontend/.env.local`. New projects sign tokens with ES256 via JWKS, so `SUPABASE_JWT_SECRET` isn't needed.

## Draft night
1. Practice first: **Start mock** (set the team count and your slot).
2. When the ESPN draft room opens, pick your league and click **Connect live draft**. Picks sync every 3s.
3. If sync fails: **Pause auto-sync** and enter picks with the **Draft** buttons.

## In season: daily lineup (recommend only)
`/lineup` (📋 Daily lineup in the header) shows, for today:
- **Moves to make in ESPN:** start or bench, each with a reason, and a checklist you tick off as you go.
- **Injury flags.**
- **Your optimal lineup.**
- **A week grid** of who plays which day.
- **This week's matchup projection** against your opponent.
- **Streaming pickups** (add/drop suggestions).

**The app never writes to ESPN.** Disney's Terms of Use (§2.B(x), §3.I) prohibit automated access and "automated gameplay". You make the moves in the ESPN app, or tap ESPN's **Quick Lineup** and then adjust.

How it decides:
- Players with no game, OUT, or in the IR slot are never started.
- Players whose game has tipped off are locked where they are.
- Value is the per-game projection, blended 70/30 with last-15-day form once a player has 3+ games.
- Streams are suggested only if they add 15+ points over the rest of the week, and never by dropping a player clearly better per game.

### Daily briefing on your Mac
```bash
scripts/install_briefing.sh          # runs at 12:00 and 18:00 (a run missed while asleep runs on wake)
scripts/install_briefing.sh run      # run now; uninstall / status also available
```
It saves the briefing for the lineup page and shows a notification. If you saved an Anthropic key, Claude adds a short summary. Because the project lives in `~/Documents`, macOS requires you to give the venv's Python **Full Disk Access** once (`run` prints the exact path if it's blocked).

## How picks are ranked
- **Proj FP:** ESPN season projection × your league's scoring weights (pulled from ESPN).
- **VORP:** points over the best player left after every team's open starting slots are filled. It is recomputed after each pick, so position scarcity changes live.
- **VONA:** points lost at the slot this player fills if you wait until your next pick. It uses snake math and ADP-based survival odds.
- **Need:** the share of the player's value that lands in your optimal starting lineup (PG, SG, SF, PF, C, G, F and 3 UTIL). Bench-only players are discounted.
- **Score:** `(0.6·VORP + 0.4·VONA) × Need`, times an optional astrology modifier.
- **Build plan:** your open slots, ranked by how much value is lost if you wait.
- **Astrology mode** (off by default): Chinese zodiac animal and element plus life-path number. Each player is compared with the current lunar year and with `USER_BIRTHDATE`. It is capped at ±10% and shown in its own column.

## Tests
```bash
backend/.venv/bin/python -m pytest -q backend/tests   # account tests run when local Supabase is up, else skip
```

## Roadmap
Phase 2: a daily and weekly lineup optimizer using NBA schedule, matchups and streaming suggestions.
