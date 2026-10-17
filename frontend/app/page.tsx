"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, type Advice, type DraftState, type Me, type PlayerRow, type Recommend, type SavedLeague } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const POSITIONS = ["", "PG", "SG", "SF", "PF", "C"];
const SLOT_ORDER = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "BE"];
const injury = (s?: string) => s?.replace(/_/g, " ").toLowerCase();
const fmt = (n: number | null | undefined, d = 0) => (n == null ? "–" : n.toLocaleString(undefined, { maximumFractionDigits: d }));

function Panel({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function Btn(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "ghost" }) {
  const { tone = "ghost", className = "", ...rest } = props;
  const base = "rounded px-2.5 py-1 text-sm disabled:opacity-40";
  const toneCls = tone === "primary" ? "bg-emerald-600 hover:bg-emerald-500 text-white" : "border border-slate-700 hover:bg-slate-800";
  return <button {...rest} className={`${base} ${toneCls} ${className}`} />;
}

export default function DraftPage() {
  const { session, loading: authLoading, enabled: accountsEnabled, signOut } = useAuth();
  const [account, setAccount] = useState<Me | null>(null);
  const [leagues, setLeagues] = useState<SavedLeague[]>([]);
  const [leagueId, setLeagueId] = useState("");
  const [st, setSt] = useState<DraftState | null>(null);
  const [rec, setRec] = useState<Recommend | null>(null);
  const [advice, setAdvice] = useState<Advice | null>(null);
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [astro, setAstro] = useState(false);
  const [astroWeight, setAstroWeight] = useState(0.03);
  const [teams, setTeams] = useState(10);
  const [slot, setSlot] = useState(1);
  const [pos, setPos] = useState("");
  const [q, setQ] = useState("");
  const [board, setBoard] = useState<PlayerRow[]>([]);
  const [busy, setBusy] = useState(false);
  const lastPick = useRef<number | null>(null);

  const run = useCallback(async <T,>(fn: () => Promise<T>) => {
    setBusy(true);
    setErr(null);
    try {
      return await fn();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const refresh = useCallback(async (force = false) => {
    try {
      const s = await api.state();
      setSt(s);
      if (force || s.current_pick !== lastPick.current) {
        lastPick.current = s.current_pick;
        setAdvice(null);
        setRec(await api.recommend(astro, astroWeight));
      }
    } catch {
      setSt(null);
    }
  }, [astro, astroWeight]);

  // (Re)load everything once auth is known and whenever the signed-in user changes
  const userId = session?.user.id;
  useEffect(() => {
    if (authLoading) return;
    setSt(null);
    setRec(null);
    lastPick.current = null;
    api.me().then(setAccount).catch(() => setErr("Backend not reachable on :8000 - start it with ./run.sh"));
    if (userId) {
      api.leagues().then((ls) => {
        setLeagues(ls);
        setLeagueId((ls.find((l) => l.is_default) ?? ls[0])?.id ?? "");
      }).catch(() => {});
    } else {
      setLeagues([]);
    }
    refresh(true);
  }, [authLoading, userId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (st) refresh(true);
  }, [astro, astroWeight]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live polling while connected to ESPN
  useEffect(() => {
    if (st?.source !== "espn" || st.complete) return;
    const t = setInterval(() => refresh(), 3000);
    return () => clearInterval(t);
  }, [st?.source, st?.complete, refresh]);

  useEffect(() => {
    if (!st) return;
    const t = setTimeout(() => api.players(pos, q).then(setBoard).catch(() => {}), 150);
    return () => clearTimeout(t);
  }, [st?.current_pick, pos, q, st]);

  const myTurn = st != null && st.on_clock === st.my_team_id;
  const canPick = st != null && !st.complete && (st.source === "sim" || !st.polling);
  const me = st ? st.rosters[String(st.my_team_id)] : undefined;
  const mySlots = useMemo(() => {
    if (!me) return [];
    return [...me.players].sort((a, b) => SLOT_ORDER.indexOf(a.slot ?? "BE") - SLOT_ORDER.indexOf(b.slot ?? "BE"));
  }, [me]);

  const doPick = (id: number) => run(async () => { await api.pick(id); await refresh(true); });

  return (
    <main className="mx-auto max-w-[1500px] space-y-3 p-4">
      {/* Header */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">🏀 Draft Assistant</h1>
          <p className="text-xs text-slate-400">
            {st ? `${st.source === "espn" ? `${st.league} · Live ESPN` : "Mock draft"} · ${st.pick_order.length} teams · ${st.rounds} rounds` : "No session"}
            {st?.saved && " · saved to your account"}
            {account && ` · Claude: ${account.profile?.has_anthropic_key ? account.profile.claude_model || "default model" : "no key (engine only)"}`}
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {session ? (
            <>
              <Link href="/lineup" className="text-slate-300 hover:text-emerald-400">📋 Daily lineup</Link>
              <Link href="/profile" className="text-slate-300 hover:text-emerald-400">👤 {account?.profile?.display_name || session.user.email}</Link>
              <button onClick={signOut} className="text-slate-500 hover:text-rose-400">Sign out</button>
            </>
          ) : accountsEnabled ? (
            <Link href="/login" className="rounded bg-emerald-600 px-3 py-1 text-white hover:bg-emerald-500">Sign in</Link>
          ) : null}
        </div>
        {st && (
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-xs text-slate-400">Pick</div>
              <div className="text-lg font-semibold">{st.complete ? "Done" : `${st.current_pick} / ${st.pick_order.length * st.rounds}`}</div>
            </div>
            <div className="text-right">
              <div className="text-xs text-slate-400">On the clock</div>
              <div className={`text-lg font-semibold ${myTurn ? "text-emerald-400" : ""}`}>
                {st.on_clock ? (myTurn ? "YOU" : st.teams[String(st.on_clock)]) : "–"}
              </div>
            </div>
            <div className={`rounded-lg px-4 py-2 text-center ${myTurn ? "bg-emerald-600 animate-pulse" : "bg-slate-800"}`}>
              <div className="text-xs">Picks until you</div>
              <div className="text-2xl font-bold">{myTurn ? "NOW" : rec?.picks_until_me ?? "–"}</div>
            </div>
          </div>
        )}
      </header>

      {/* Controls */}
      <Panel title="Session">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-slate-400">Mock:</span>
          <label>Teams <input type="number" min={4} max={16} value={teams} onChange={(e) => setTeams(+e.target.value)} className="w-14 rounded bg-slate-800 px-1" /></label>
          <label>My slot <input type="number" min={1} max={teams} value={slot} onChange={(e) => setSlot(+e.target.value)} className="w-14 rounded bg-slate-800 px-1" /></label>
          <Btn disabled={busy} onClick={() => run(async () => { await api.simStart(teams, slot); await refresh(true); })}>Start mock</Btn>
          <span className="mx-2 h-5 w-px bg-slate-700" />
          <span className="text-slate-400">ESPN:</span>
          {!session ? (
            <Link href="/login" className="text-emerald-400 hover:underline">Sign in to connect your ESPN league</Link>
          ) : leagues.length ? (
            <>
              <select value={leagueId} onChange={(e) => setLeagueId(e.target.value)} className="rounded bg-slate-800 px-1 py-0.5">
                {leagues.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.team_name})</option>)}
              </select>
              <Btn disabled={busy || !leagueId} onClick={() => run(async () => { await api.espnConnect(leagueId); await refresh(true); })}>Connect live draft</Btn>
            </>
          ) : (
            <Link href="/profile" className="rounded bg-emerald-600 px-2.5 py-1 text-white hover:bg-emerald-500">Add your ESPN league</Link>
          )}
          {st?.source === "espn" && !st.complete && (
            <>
              <Btn onClick={() => run(async () => { await api.polling(!st.polling); await refresh(true); })}>{st.polling ? "Pause auto-sync" : "Resume auto-sync"}</Btn>
              <Btn onClick={() => run(async () => { await api.sync(); await refresh(true); })}>Sync now</Btn>
            </>
          )}
          {st && canPick && <Btn onClick={() => run(async () => { await api.undo(); await refresh(true); })}>Undo</Btn>}
          <span className="mx-2 h-5 w-px bg-slate-700" />
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={astro} onChange={(e) => setAstro(e.target.checked)} /> Numerology + Chinese astrology
          </label>
          {astro && !account?.profile?.birthdate && <Link href={session ? "/profile" : "/login"} className="text-xs text-amber-400 hover:underline">add your birthdate for personal compatibility</Link>}
          {astro && (
            <label className="flex items-center gap-1 text-xs">
              weight ±{Math.round(astroWeight * 100)}%
              <input type="range" min={0.01} max={0.1} step={0.01} value={astroWeight} onChange={(e) => setAstroWeight(+e.target.value)} />
            </label>
          )}
        </div>
        {(err || st?.sync_error) && <p className="mt-2 text-sm text-rose-400">{err ?? `ESPN sync: ${st?.sync_error}`}</p>}
        {st?.source === "espn" && st.complete && <p className="mt-1 text-xs text-slate-500">Draft complete — stopped syncing with ESPN.</p>}
        {st?.source === "espn" && st.polling && <p className="mt-1 text-xs text-slate-500">Auto-syncing from ESPN every 3s. Pause auto-sync to enter picks manually.</p>}
      </Panel>

      {st && (
        <div className="grid gap-3 lg:grid-cols-[1fr_360px]">
          <div className="space-y-3">
            {/* Recommendations */}
            <Panel
              title="Recommendations"
              right={
                <Btn tone="primary" disabled={adviceLoading || !rec?.recommendations.length} onClick={async () => {
                  setAdviceLoading(true);
                  try { setAdvice(await api.advice(astro, astroWeight)); } catch (e) { setErr((e as Error).message); } finally { setAdviceLoading(false); }
                }}>
                  {adviceLoading ? "Asking Claude…" : "Ask Claude"}
                </Btn>
              }
            >
              {advice && (
                <div className="mb-3 rounded border border-emerald-700 bg-emerald-950/40 p-3 text-sm">
                  <div className="font-semibold text-emerald-300">
                    {advice.source === "claude" ? "Claude says:" : "Engine pick:"} {advice.player_name}
                  </div>
                  <p className="mt-1">{advice.rationale}</p>
                  {advice.wait_on && <p className="mt-1 text-slate-300">Can wait on: {advice.wait_on}</p>}
                  {advice.strategy_note && <p className="mt-1 text-slate-300">Plan: {advice.strategy_note}</p>}
                  {advice.error && <p className="mt-1 text-xs text-amber-400">{advice.error}</p>}
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs text-slate-400">
                    <tr className="text-right [&>th:first-child]:text-left [&>th:nth-child(2)]:text-left">
                      <th>Player</th><th>Pos</th><th>Fills</th><th>Proj FP</th><th>FP/G</th><th title="Value over replacement at position">VORP</th>
                      <th title="Value lost if you wait until your next pick">VONA</th><th title="Share of value that lands in your starting lineup">Need</th>
                      <th title="Chance he is still there at your next pick">Survive</th><th>ADP</th>{astro && <th>Astro</th>}<th>Score</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rec?.recommendations.map((r, i) => (
                      <tr key={r.player_id} className={`border-t border-slate-800 text-right ${i === 0 ? "bg-emerald-950/30" : ""}`}>
                        <td className="py-1 text-left">
                          <span className="font-medium">{r.name}</span> <span className="text-xs text-slate-500">{r.team}</span>
                          {r.injury && r.injury !== "ACTIVE" && <span className="ml-1 rounded bg-rose-900 px-1 text-xs">{injury(r.injury)}</span>}
                        </td>
                        <td className="text-left">{r.positions.join("/")}</td>
                        <td>{r.slot}</td>
                        <td>{fmt(r.fpts)}</td>
                        <td>{fmt(r.fpg, 1)}</td>
                        <td>{fmt(r.vorp)}</td>
                        <td className={r.vona > 150 ? "text-amber-300" : ""}>{fmt(r.vona)}</td>
                        <td>{Math.round(r.need * 100)}%</td>
                        <td className={r.survive_next < 0.3 ? "text-rose-300" : r.survive_next > 0.7 ? "text-emerald-300" : ""}>{Math.round(r.survive_next * 100)}%</td>
                        <td>{fmt(r.adp, 1)}</td>
                        {astro && <td title={r.astro_detail?.why}>{r.astro_detail ? `${r.astro_detail.animal} ${r.astro > 0 ? "+" : ""}${r.astro}` : "–"}</td>}
                        <td className="font-semibold">{fmt(r.score)}</td>
                        <td>{canPick && <Btn className="ml-2" onClick={() => doPick(r.player_id)}>{st.source === "sim" && !myTurn ? "Pick" : "Draft"}</Btn>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            {/* Build plan */}
            <Panel title="Build plan — which slots to fill first">
              <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {rec?.build_plan.map((p) => (
                  <div key={p.slot} className="rounded border border-slate-800 p-2 text-sm">
                    <div className="flex justify-between"><span className="font-semibold">{p.slot}{p.open > 1 ? ` ×${p.open}` : ""}</span><span className={p.dropoff > 150 ? "text-amber-300" : "text-slate-400"}>−{fmt(p.dropoff)} if you wait</span></div>
                    <div className="text-xs text-slate-400">Best now: {p.best_now} ({fmt(p.best_now_fpts)})</div>
                    <div className="text-xs text-slate-500">Expected next pick: {fmt(p.expected_later_fpts)}</div>
                  </div>
                ))}
                {!rec?.build_plan.length && <p className="text-sm text-slate-500">All starting slots filled — draft for depth / upside.</p>}
              </div>
            </Panel>

            {/* Board */}
            <Panel
              title="Available players"
              right={
                <div className="flex gap-2">
                  <select value={pos} onChange={(e) => setPos(e.target.value)} className="rounded bg-slate-800 px-1 text-sm">
                    {POSITIONS.map((p) => <option key={p} value={p}>{p || "All"}</option>)}
                  </select>
                  <input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} className="rounded bg-slate-800 px-2 text-sm" />
                </div>
              }
            >
              <div className="max-h-96 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-900 text-xs text-slate-400">
                    <tr className="text-right [&>th:first-child]:text-left [&>th:nth-child(2)]:text-left"><th>Player</th><th>Pos</th><th>Proj FP</th><th>FP/G</th><th>ADP</th><th></th></tr>
                  </thead>
                  <tbody>
                    {board.map((p) => (
                      <tr key={p.player_id} className="border-t border-slate-800 text-right">
                        <td className="py-0.5 text-left">{p.name} <span className="text-xs text-slate-500">{p.team}</span>{p.injury && p.injury !== "ACTIVE" && <span className="ml-1 rounded bg-rose-900 px-1 text-xs">{injury(p.injury)}</span>}</td>
                        <td className="text-left">{p.positions.join("/")}</td>
                        <td>{fmt(p.fpts)}</td><td>{fmt(p.fpg, 1)}</td><td>{fmt(p.adp, 1)}</td>
                        <td>{canPick && <Btn className="ml-2 py-0" onClick={() => doPick(p.player_id)}>Draft</Btn>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>

          {/* Right column */}
          <div className="space-y-3">
            <Panel title="My roster" right={<span className="text-xs text-slate-400">Starters {fmt(me?.projected_starters_fpts)} FP</span>}>
              <ul className="space-y-0.5 text-sm">
                {mySlots.map((p) => (
                  <li key={p.player_id} className="flex justify-between">
                    <span><span className="inline-block w-10 text-xs text-slate-500">{p.slot}</span>{p.name}</span>
                    <span className="text-slate-400">{fmt(p.fpts)}</span>
                  </li>
                ))}
                {rec?.my_open_slots.map((s, i) => (
                  <li key={`open-${s}-${i}`} className="text-slate-600"><span className="inline-block w-10 text-xs">{s}</span>open</li>
                ))}
              </ul>
            </Panel>

            <Panel title="League">
              <ul className="space-y-1 text-xs">
                {Object.entries(st.rosters)
                  .sort((a, b) => b[1].projected_starters_fpts - a[1].projected_starters_fpts)
                  .map(([tid, r]) => (
                    <li key={tid} className={`rounded p-1 ${+tid === st.my_team_id ? "bg-emerald-950/40" : ""} ${+tid === st.on_clock ? "ring-1 ring-amber-500" : ""}`}>
                      <div className="flex justify-between"><span className="font-medium">{r.name}</span><span className="text-slate-400">{fmt(r.projected_starters_fpts)} FP</span></div>
                      {rec?.opponent_open_slots[tid] && <div className="text-slate-500">needs: {rec.opponent_open_slots[tid].join(", ") || "bench"}</div>}
                    </li>
                  ))}
              </ul>
            </Panel>

            <Panel title="Recent picks">
              <ul className="max-h-72 space-y-0.5 overflow-y-auto text-xs">
                {[...st.picks].reverse().slice(0, 30).map((p) => (
                  <li key={p.pick} className={p.team_id === st.my_team_id ? "text-emerald-300" : ""}>
                    <span className="inline-block w-8 text-slate-500">#{p.pick}</span>{p.name} <span className="text-slate-500">({p.positions.join("/")}) → {st.teams[String(p.team_id)]}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>
        </div>
      )}

      {!st && !err && (
        <p className="text-slate-400">
          Start a mock draft to practice{leagues.length ? ", or connect to your ESPN league when the draft opens." : session ? <>. To use your real draft, <Link href="/profile" className="text-emerald-400 hover:underline">add your ESPN league</Link>.</> : <>. <Link href="/login" className="text-emerald-400 hover:underline">Sign in</Link> to connect your real ESPN draft and save your drafts.</>}
        </p>
      )}
    </main>
  );
}
