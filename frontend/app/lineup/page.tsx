"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { seasonApi, type Briefing, type Lineup, type Matchup, type Stream, type Week } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const fmt = (n: number | null | undefined, d = 0) => (n == null ? "–" : n.toLocaleString(undefined, { maximumFractionDigits: d }));
const injury = (s: string) => s.replace(/_/g, " ").toLowerCase();
const tip = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const CHECKS = "draft-assistant.lineup-checks";

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

function loadChecks(date: string): Record<string, boolean> {
  try {
    const all = JSON.parse(localStorage.getItem(CHECKS) ?? "{}");
    return all[date] ?? {};
  } catch {
    return {};
  }
}

function saveChecks(date: string, checks: Record<string, boolean>) {
  try {
    localStorage.setItem(CHECKS, JSON.stringify({ [date]: checks })); // keep only today's
  } catch {}
}

export default function LineupPage() {
  const { session, loading } = useAuth();
  const router = useRouter();
  const [lineup, setLineup] = useState<Lineup | null>(null);
  const [week, setWeek] = useState<Week | null>(null);
  const [matchup, setMatchup] = useState<Matchup | null>(null);
  const [streams, setStreams] = useState<Stream[] | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    setErr(null);
    try {
      const lu = await seasonApi.lineup(undefined, refresh);
      setLineup(lu);
      setChecks(loadChecks(lu.date));
      const [w, m, b] = await Promise.all([seasonApi.week(), seasonApi.matchup(), seasonApi.briefing()]);
      setWeek(w);
      setMatchup(m);
      setBriefing(b);
      setStreams(await seasonApi.streams()); // slowest: free-agent pool
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (loading) return;
    if (!session) router.replace("/login");
    else load();
  }, [session, loading, load, router]);

  const toggle = (key: string) => {
    if (!lineup) return;
    const next = { ...checks, [key]: !checks[key] };
    setChecks(next);
    saveChecks(lineup.date, next);
  };

  if (loading || !session) return <main className="p-6 text-sm text-slate-400">Loading…</main>;

  const empty = lineup && lineup.roster_size === 0;
  const done = lineup ? lineup.moves.filter((m) => checks[`${m.action}-${m.player_id}`]).length : 0;

  return (
    <main className="mx-auto max-w-[1300px] space-y-3 p-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">📋 Daily lineup</h1>
          <p className="text-xs text-slate-400">
            {lineup ? `${new Date(lineup.date + "T12:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })} · ${lineup.games_today} of your players play today` : "…"}
            {" · Recommendations only: make the moves in the ESPN app (or tap Quick Lineup, then adjust)."}
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <button onClick={() => load(true)} disabled={busy} className="rounded border border-slate-700 px-2.5 py-1 hover:bg-slate-800 disabled:opacity-40">
            {busy ? "Refreshing…" : "Refresh from ESPN"}
          </button>
          <Link href="/" className="text-emerald-400 hover:underline">← Draft</Link>
          <Link href="/profile" className="text-slate-400 hover:text-emerald-400">Profile</Link>
        </div>
      </header>

      {err && <p className="text-sm text-rose-400">{err}{err.includes("Profile") && <> · <Link href="/profile" className="underline">Open Profile</Link></>}</p>}

      {briefing && (
        <Panel title="Briefing" right={<span className="text-xs text-slate-500">from your Mac · {briefing.generated_at.replace("T", " ")}</span>}>
          <p className="font-medium">{briefing.headline}</p>
          {briefing.summary && <p className="mt-1 whitespace-pre-line text-sm text-slate-300">{briefing.summary}</p>}
        </Panel>
      )}

      {empty && <Panel title="No roster yet"><p className="text-sm text-slate-400">Your roster is empty. Lineups appear here after your draft.</p></Panel>}

      {lineup && !empty && (
        <div className="grid gap-3 lg:grid-cols-[1fr_380px]">
          <div className="space-y-3">
            <Panel title="Make these moves in ESPN" right={<span className="text-xs text-slate-400">{done}/{lineup.moves.length} done · expected {fmt(lineup.expected_points)} FP</span>}>
              {lineup.moves.length === 0 ? (
                <p className="text-sm text-emerald-400">Your ESPN lineup is already optimal for today ✓</p>
              ) : (
                <ul className="space-y-1">
                  {lineup.moves.map((m) => {
                    const key = `${m.action}-${m.player_id}`;
                    return (
                      <li key={key}>
                        <label className={`flex cursor-pointer items-center gap-2 rounded p-1.5 text-sm hover:bg-slate-800 ${checks[key] ? "text-slate-500 line-through" : ""}`}>
                          <input type="checkbox" checked={!!checks[key]} onChange={() => toggle(key)} />
                          <span className={`w-14 rounded px-1.5 text-center text-xs ${m.action === "start" ? "bg-emerald-900" : "bg-slate-700"}`}>{m.action === "start" ? "START" : "BENCH"}</span>
                          <span className="font-medium">{m.name}</span>
                          <span className="text-slate-400">{m.action === "start" ? `→ ${m.slot}` : "→ BE"} · {m.why}</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
              {lineup.flags.length > 0 && (
                <ul className="mt-2 space-y-0.5 text-sm text-amber-300">{lineup.flags.map((f) => <li key={f}>⚠ {f}</li>)}</ul>
              )}
            </Panel>

            <Panel title="Optimal lineup today">
              <table className="w-full text-sm">
                <thead className="text-xs text-slate-400">
                  <tr className="text-left"><th>Slot</th><th>Player</th><th>Game</th><th className="pr-4 text-right">FP/G</th><th>In ESPN now</th></tr>
                </thead>
                <tbody>
                  {lineup.lineup.map((r) => (
                    <tr key={r.player_id} className={`border-t border-slate-800 ${r.target === "BE" || r.target === "IR" ? "text-slate-500" : ""}`}>
                      <td className="py-1 font-mono text-xs">{r.target}</td>
                      <td>
                        {r.name} <span className="text-xs text-slate-500">{r.team} · {r.positions.join("/")}</span>
                        {r.injury !== "ACTIVE" && <span className="ml-1 rounded bg-rose-900 px-1 text-xs">{injury(r.injury)}</span>}
                        {r.locked && <span className="ml-1 rounded bg-slate-700 px-1 text-xs">locked</span>}
                      </td>
                      <td className="text-xs">{r.game ? `${r.game.home ? "vs" : "@"} ${r.game.opp} · ${tip(r.game.start)}` : "no game"}</td>
                      <td className="pr-4 text-right">{fmt(r.fpg, 1)}</td>
                      <td className="text-xs text-slate-400">{r.current}{r.current !== r.target && !r.locked && ((r.current === "BE") !== (r.target === "BE")) ? " ✱" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>

            {week && (
              <Panel title={`Matchup week ${week.matchup_period} · games`}>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="text-slate-400">
                      <tr>
                        <th className="text-left">Player</th>
                        {week.days.map((d) => (
                          <th key={d.sp} className={`px-1 ${d.sp === week.today_sp ? "text-emerald-400" : ""}`}>
                            {new Date(d.date + "T12:00").toLocaleDateString(undefined, { weekday: "narrow", day: "numeric" })}
                          </th>
                        ))}
                        <th>Left</th>
                      </tr>
                    </thead>
                    <tbody>
                      {week.grid.map((r) => (
                        <tr key={r.player_id} className="border-t border-slate-800">
                          <td className="whitespace-nowrap py-0.5 pr-2">{r.name}</td>
                          {r.cells.map((c) => {
                            const starting = week.starts[String(c.sp)]?.includes(r.player_id);
                            return (
                              <td key={c.sp} title={c.game ?? ""} className={`px-1 text-center ${c.sp < week.today_sp ? "opacity-40" : ""}`}>
                                {c.game ? <span className={starting ? "text-emerald-400" : "text-slate-500"}>{starting ? "●" : "○"}</span> : ""}
                              </td>
                            );
                          })}
                          <td className="text-center font-semibold">{r.games_left}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-1 text-xs text-slate-500">● start · ○ game but benched (optimal) · blank = no game</p>
                </div>
              </Panel>
            )}
          </div>

          <div className="space-y-3">
            {matchup && (
              <Panel title={`This week · ${matchup.days_left} days left`}>
                {matchup.opponent ? (
                  <div className="space-y-2 text-sm">
                    {[matchup.me, matchup.opponent].map((side, i) => (
                      <div key={i} className="flex justify-between">
                        <span className={i === 0 ? "font-semibold text-emerald-300" : ""}>{side.name}</span>
                        <span>{fmt(side.current)} + <span className="text-slate-400">{fmt(side.remaining)} proj</span> · {side.starts_left} starts</span>
                      </div>
                    ))}
                    <div className={`text-right font-semibold ${(matchup.projected_margin ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      Projected {(matchup.projected_margin ?? 0) >= 0 ? "win" : "loss"} by {fmt(Math.abs(matchup.projected_margin ?? 0))}
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-slate-400">Opponent not set yet · your projection: {fmt(matchup.me.remaining)} FP over {matchup.me.starts_left} starts</p>
                )}
              </Panel>
            )}

            <Panel title="Streaming pickups">
              {streams === null ? (
                <p className="text-sm text-slate-500">Checking free agents…</p>
              ) : streams.length === 0 ? (
                <p className="text-sm text-slate-400">No free agent beats your roster enough this week.</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {streams.map((s) => (
                    <li key={s.add.player_id} className="rounded border border-slate-800 p-2">
                      <div className="flex justify-between">
                        <span>Add <b>{s.add.name}</b> <span className="text-xs text-slate-500">{s.add.team} · {s.add.positions.join("/")}</span></span>
                        <span className="font-semibold text-emerald-400">+{fmt(s.gain)} FP</span>
                      </div>
                      <div className="text-xs text-slate-400">
                        {s.add.games_left} games left · {fmt(s.add.fpg, 1)} FP/G — drop {s.drop.name} ({s.drop.games_left} games, {fmt(s.drop.fpg, 1)} FP/G)
                      </div>
                      <div className="text-xs text-slate-500">{s.season_note}</div>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-xs text-slate-500">Suggestions only. Make adds/drops in ESPN yourself.</p>
            </Panel>
          </div>
        </div>
      )}
    </main>
  );
}
