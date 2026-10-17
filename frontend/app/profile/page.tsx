"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DraftSummary, type LeagueInfo, type Profile, type SavedLeague } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { clearLegacySettings, loadLegacySettings, type LegacySettings } from "@/lib/settings";

const input = "w-full rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm outline-none focus:border-emerald-500";
const btn = "rounded border border-slate-700 px-2.5 py-1 text-sm hover:bg-slate-800 disabled:opacity-40";
const primary = "rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-500 disabled:opacity-40";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <h2 className="font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

function AddLeague({ onSaved }: { onSaved: () => void }) {
  const [leagueId, setLeagueId] = useState("");
  const [season, setSeason] = useState(2027);
  const [s2, setS2] = useState("");
  const [swid, setSwid] = useState("");
  const [info, setInfo] = useState<LeagueInfo | null>(null);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const creds = { espn_league_id: leagueId, season, espn_s2: s2 || null, swid: swid || null };

  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const i = await api.lookupLeague(creds);
      setInfo(i);
      setTeamId(i.my_team_id);
      setMsg({ ok: true, text: `Found “${i.name}” · ${i.teams.length} teams${i.my_team_id ? "" : " — pick your team"}` });
    } catch (e) {
      setInfo(null);
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!teamId) return;
    setBusy(true);
    try {
      await api.saveLeague({ ...creds, team_id: teamId });
      setLeagueId(""); setS2(""); setSwid(""); setInfo(null); setTeamId(null); setMsg(null);
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded border border-dashed border-slate-700 p-3">
      <div className="text-sm font-medium">Add an ESPN league</div>
      <div className="grid grid-cols-[1fr_110px] gap-3">
        <Field label="League ID" hint={<>The number after <code>leagueId=</code> in your league URL.</>}>
          <input className={input} inputMode="numeric" placeholder="e.g. 1234567" value={leagueId} onChange={(e) => { setLeagueId(e.target.value.replace(/\D/g, "")); setInfo(null); }} />
        </Field>
        <Field label="Season" hint="2026-27 = 2027">
          <input className={input} type="number" value={season} onChange={(e) => setSeason(+e.target.value)} />
        </Field>
      </div>
      <details className="text-xs text-slate-400">
        <summary className="cursor-pointer text-sm text-slate-300">Private league? Add your ESPN cookies (how)</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>Log in at espn.com in Chrome.</li>
          <li>DevTools (⌥⌘I) → <b>Application</b> → <b>Cookies</b> → <code>https://www.espn.com</code>.</li>
          <li>Copy <code>espn_s2</code> and <code>SWID</code> (keep the braces). They&apos;re stored encrypted.</li>
        </ol>
      </details>
      <div className="grid gap-3 sm:grid-cols-2">
        <input className={input} type="password" placeholder="espn_s2" value={s2} onChange={(e) => setS2(e.target.value)} autoComplete="off" />
        <input className={input} type="password" placeholder="SWID {XXXXXXXX-…}" value={swid} onChange={(e) => setSwid(e.target.value)} autoComplete="off" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className={btn} disabled={!leagueId || busy} onClick={test}>{busy && !info ? "Checking…" : "Test connection"}</button>
        {info && (
          <>
            <select className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-sm" value={teamId ?? ""} onChange={(e) => setTeamId(e.target.value ? +e.target.value : null)}>
              <option value="">Select your team…</option>
              {info.teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <button className={primary} disabled={!teamId || busy} onClick={save}>Save league</button>
          </>
        )}
      </div>
      {msg && <p className={`text-sm ${msg.ok ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</p>}
    </div>
  );
}

export default function ProfilePage() {
  const { session, loading, signOut } = useAuth();
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [form, setForm] = useState({ display_name: "", birthdate: "", claude_model: "" });
  const [newKey, setNewKey] = useState("");
  const [leagues, setLeagues] = useState<SavedLeague[]>([]);
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [legacy, setLegacy] = useState<LegacySettings | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const leaving = useRef(false);

  const load = useCallback(async () => {
    const [p, l, d] = await Promise.all([api.profile(), api.leagues(), api.drafts()]);
    setProfile(p);
    setForm({ display_name: p.display_name ?? "", birthdate: p.birthdate ?? "", claude_model: p.claude_model ?? "" });
    setLeagues(l);
    setDrafts(d);
  }, []);

  useEffect(() => {
    if (loading) return;
    if (!session) {
      if (!leaving.current) router.replace("/login");
      return;
    }
    load().catch((e) => setMsg({ ok: false, text: (e as Error).message }));
    setLegacy(loadLegacySettings());
  }, [session, loading, load, router]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      if (ok) setMsg({ ok: true, text: ok });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const importLegacy = () => act(async () => {
    if (!legacy) return;
    await api.updateProfile({
      ...(legacy.anthropicKey ? { anthropic_key: legacy.anthropicKey } : {}),
      ...(legacy.birthdate ? { birthdate: legacy.birthdate } : {}),
      ...(legacy.claudeModel ? { claude_model: legacy.claudeModel } : {}),
    });
    if (legacy.leagueId && legacy.teamId) {
      await api.saveLeague({ espn_league_id: legacy.leagueId, season: legacy.year, espn_s2: legacy.espnS2 || null, swid: legacy.swid || null, team_id: legacy.teamId });
    }
    clearLegacySettings();
    setLegacy(null);
    await load();
  }, "Imported settings from this browser and removed the local copy.");

  if (loading || !session) return <main className="p-6 text-sm text-slate-400">Loading…</main>;

  return (
    <main className="mx-auto max-w-2xl space-y-5 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Profile</h1>
        <Link href="/" className="text-sm text-emerald-400 hover:underline">← Back to draft</Link>
      </div>

      {legacy && (
        <div className="flex items-center justify-between gap-3 rounded border border-amber-700 bg-amber-950/40 p-3 text-sm">
          <span>This browser has settings from before accounts{legacy.leagueId ? ` (league ${legacy.leagueId})` : ""}. Move them into your profile?</span>
          <span className="flex gap-2">
            <button className={primary} disabled={busy} onClick={importLegacy}>Import</button>
            <button className={btn} onClick={() => { clearLegacySettings(); setLegacy(null); }}>Discard</button>
          </span>
        </div>
      )}
      {msg && <p className={`text-sm ${msg.ok ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</p>}

      <Section title="Account">
        <div className="flex items-center justify-between text-sm">
          <span className="text-slate-300">{session.user.email}</span>
          <button className={btn} onClick={async () => { leaving.current = true; await signOut(); router.replace("/"); }}>Sign out</button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Display name"><input className={input} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></Field>
          <Field label="Birthdate" hint="Optional — for numerology & Chinese astrology mode.">
            <input className={input} type="date" value={form.birthdate} onChange={(e) => setForm({ ...form, birthdate: e.target.value })} />
          </Field>
        </div>
        <button className={primary} disabled={busy} onClick={() => act(async () => {
          setProfile(await api.updateProfile({ display_name: form.display_name || null, birthdate: form.birthdate || null }));
        }, "Profile saved.")}>Save profile</button>
      </Section>

      <Section title="Claude">
        <Field label="Anthropic API key" hint="Powers “Ask Claude”. Stored encrypted on the server; never shown again after saving.">
          {profile?.has_anthropic_key ? (
            <div className="flex items-center gap-2 text-sm">
              <span className="whitespace-nowrap text-emerald-400">Saved ✓</span>
              <input className={input} type="password" placeholder="Paste a new key to replace" value={newKey} onChange={(e) => setNewKey(e.target.value)} autoComplete="off" />
              <button className={btn} disabled={!newKey || busy} onClick={() => act(async () => { setProfile(await api.updateProfile({ anthropic_key: newKey })); setNewKey(""); }, "Key replaced.")}>Replace</button>
              <button className={btn} disabled={busy} onClick={() => act(async () => setProfile(await api.updateProfile({ anthropic_key: "" })), "Key removed.")}>Remove</button>
            </div>
          ) : (
            <div className="flex gap-2">
              <input className={input} type="password" placeholder="sk-ant-…" value={newKey} onChange={(e) => setNewKey(e.target.value)} autoComplete="off" />
              <button className={primary} disabled={!newKey || busy} onClick={() => act(async () => { setProfile(await api.updateProfile({ anthropic_key: newKey })); setNewKey(""); }, "Key saved.")}>Save key</button>
            </div>
          )}
        </Field>
        <Field label="Model" hint="Leave blank for the default (claude-opus-5-5).">
          <div className="flex gap-2">
            <input className={input} placeholder="claude-opus-5-5" value={form.claude_model} onChange={(e) => setForm({ ...form, claude_model: e.target.value })} />
            <button className={btn} disabled={busy} onClick={() => act(async () => setProfile(await api.updateProfile({ claude_model: form.claude_model || null })), "Model saved.")}>Save</button>
          </div>
        </Field>
      </Section>

      <Section title="ESPN leagues">
        {leagues.length === 0 && <p className="text-sm text-slate-400">No leagues yet.</p>}
        <ul className="space-y-2">
          {leagues.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-slate-800 p-2 text-sm">
              <div>
                <div className="font-medium">{l.name} {l.is_default && <span className="ml-1 rounded bg-emerald-900 px-1.5 text-xs">default</span>}</div>
                <div className="text-xs text-slate-400">
                  #{l.espn_league_id} · {l.season} · team: {l.team_name} · {l.has_cookies ? "cookies saved 🔒" : "public (no cookies)"}
                </div>
              </div>
              <div className="flex gap-2">
                {!l.is_default && <button className={btn} disabled={busy} onClick={() => act(async () => setLeagues(await api.setDefaultLeague(l.id)))}>Make default</button>}
                <button className={btn} disabled={busy} onClick={() => act(async () => setLeagues(await api.deleteLeague(l.id)), "League removed.")}>Remove</button>
              </div>
            </li>
          ))}
        </ul>
        <AddLeague onSaved={() => { load(); setMsg({ ok: true, text: "League saved." }); }} />
      </Section>

      <Section title="Draft history">
        {drafts.length === 0 && <p className="text-sm text-slate-400">No drafts yet.</p>}
        <ul className="space-y-1 text-sm">
          {drafts.map((d) => (
            <li key={d.id} className="flex justify-between">
              <span>
                {d.source === "espn" ? (leagues.find((l) => l.id === d.league_id)?.name ?? "ESPN draft") : `Mock (${String(d.config.teams ?? "?")} teams, slot ${String(d.config.my_slot ?? "?")})`}
                <span className={`ml-2 text-xs ${d.status === "active" ? "text-emerald-400" : "text-slate-500"}`}>{d.status}</span>
              </span>
              <span className="text-xs text-slate-400">{d.picks_made} picks · {new Date(d.updated_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      </Section>
    </main>
  );
}
