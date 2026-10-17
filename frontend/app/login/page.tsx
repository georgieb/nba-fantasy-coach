"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

const input = "w-full rounded border border-slate-700 bg-slate-800 px-2 py-2 text-sm outline-none focus:border-emerald-500";

export default function LoginPage() {
  const { session, enabled } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (session) router.replace("/profile");
  }, [session, router]);

  if (!enabled || !supabase) {
    return <main className="mx-auto max-w-sm p-6 text-sm text-slate-400">Accounts aren&apos;t configured on this server. <Link href="/" className="text-emerald-400">Continue as guest</Link></main>;
  }
  const sb = supabase;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const { data, error } = mode === "signin"
      ? await sb.auth.signInWithPassword({ email, password })
      : await sb.auth.signUp({ email, password, options: { emailRedirectTo: `${location.origin}/profile` } });
    setBusy(false);
    if (error) return setMsg({ ok: false, text: error.message });
    if (mode === "signup" && !data.session) setMsg({ ok: true, text: "Check your email to confirm your account." });
  };

  const magicLink = async () => {
    if (!email) return setMsg({ ok: false, text: "Enter your email first." });
    setBusy(true);
    const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: `${location.origin}/profile` } });
    setBusy(false);
    setMsg(error ? { ok: false, text: error.message } : { ok: true, text: `Magic link sent to ${email}.` });
  };

  return (
    <main className="mx-auto max-w-sm space-y-5 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">🏀 {mode === "signin" ? "Sign in" : "Create account"}</h1>
        <Link href="/" className="text-sm text-slate-400 hover:text-emerald-400">Continue as guest</Link>
      </div>
      <div className="flex rounded border border-slate-700 text-sm">
        {(["signin", "signup"] as const).map((m) => (
          <button key={m} onClick={() => { setMode(m); setMsg(null); }} className={`flex-1 py-1.5 ${mode === m ? "bg-slate-700" : "hover:bg-slate-800"}`}>
            {m === "signin" ? "Sign in" : "Sign up"}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="space-y-3">
        <input className={input} type="email" required placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        <input className={input} type="password" required minLength={mode === "signup" ? 8 : undefined} placeholder={mode === "signup" ? "Password (8+ characters)" : "Password"} value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "signin" ? "current-password" : "new-password"} />
        <button disabled={busy} className="w-full rounded bg-emerald-600 py-2 text-sm text-white hover:bg-emerald-500 disabled:opacity-40">
          {busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}
        </button>
      </form>
      <div className="flex items-center gap-2 text-xs text-slate-500"><span className="h-px flex-1 bg-slate-800" />or<span className="h-px flex-1 bg-slate-800" /></div>
      <button disabled={busy} onClick={magicLink} className="w-full rounded border border-slate-700 py-2 text-sm hover:bg-slate-800 disabled:opacity-40">
        Email me a magic link
      </button>
      {msg && <p className={`text-sm ${msg.ok ? "text-emerald-400" : "text-rose-400"}`}>{msg.text}</p>}
    </main>
  );
}
