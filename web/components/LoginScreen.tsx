/**
 * Login screen (System ① BYOK).
 *
 * Primary: Google OAuth (signInWithOAuth → redirects to Google → back to this origin where the
 * browser client consumes the session and the auth gate opens). No domain / no email infra needed.
 * Secondary (behind a disclosure): magic-link email — only works once a custom SMTP sender is
 * configured in Supabase, so it's de-emphasized to avoid sending people to a dead end.
 */
import { useState, FormEvent } from "react";
import { getSupabaseBrowser } from "@/lib/supabase-browser";

type Status = "idle" | "sending" | "sent" | "error";

export default function LoginScreen() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errMsg, setErrMsg] = useState("");
  const [googleLoading, setGoogleLoading] = useState(false);
  const [showEmail, setShowEmail] = useState(false);

  async function handleGoogle() {
    setGoogleLoading(true);
    setErrMsg("");
    const { error } = await getSupabaseBrowser().auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: typeof window !== "undefined" ? window.location.origin : undefined,
      },
    });
    // On success the page redirects to Google; we only land here if the call itself failed.
    if (error) {
      setGoogleLoading(false);
      setErrMsg(error.message);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setStatus("sending");
    setErrMsg("");
    const { error } = await getSupabaseBrowser().auth.signInWithOtp({
      email: email.trim(),
      options: {
        emailRedirectTo:
          typeof window !== "undefined" ? window.location.origin : undefined,
      },
    });
    if (error) {
      setStatus("error");
      setErrMsg(error.message);
    } else {
      setStatus("sent");
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-50 dark:bg-neutral-950 px-4">
      <div className="w-full max-w-sm rounded-2xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-8 shadow-sm">
        <h1 className="text-lg font-semibold text-neutral-900 dark:text-neutral-100">登录</h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          用 Google 一键登录,首次登录会自动为你创建一个工作区。
        </p>

        <button
          type="button"
          onClick={handleGoogle}
          disabled={googleLoading}
          className="mt-6 flex w-full items-center justify-center gap-2 rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-950 px-3 py-2.5 text-sm font-medium text-neutral-900 dark:text-neutral-100 hover:bg-neutral-50 dark:hover:bg-neutral-900 disabled:opacity-50"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" />
            <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" />
            <path fill="#FBBC05" d="M3.964 10.706A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.706V4.962H.957A8.997 8.997 0 0 0 0 9c0 1.452.348 2.827.957 4.038l3.007-2.332Z" />
            <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.962L3.964 7.294C4.672 5.167 6.656 3.58 9 3.58Z" />
          </svg>
          {googleLoading ? "跳转中…" : "用 Google 登录"}
        </button>

        {errMsg && !showEmail && (
          <p className="mt-3 text-sm text-red-600 dark:text-red-400">出错了:{errMsg}</p>
        )}

        {!showEmail ? (
          <button
            type="button"
            onClick={() => setShowEmail(true)}
            className="mt-4 block w-full text-center text-xs text-neutral-400 dark:text-neutral-500 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
          >
            用邮箱登录
          </button>
        ) : status === "sent" ? (
          <div className="mt-6 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900 p-4 text-sm text-emerald-800 dark:text-emerald-300">
            ✅ 链接已发到 <span className="font-medium">{email}</span> —— 去邮箱点链接即可登录。
            <button
              type="button"
              onClick={() => setStatus("idle")}
              className="mt-2 block text-emerald-700 dark:text-emerald-400 underline underline-offset-2"
            >
              换个邮箱
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="mt-6 space-y-3">
            <div className="text-xs text-neutral-400 dark:text-neutral-500">或用邮箱链接登录:</div>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-950 px-3 py-2 text-sm text-neutral-900 dark:text-neutral-100 outline-none focus:ring-2 focus:ring-neutral-900 dark:focus:ring-neutral-300"
            />
            <button
              type="submit"
              disabled={status === "sending"}
              className="w-full rounded-lg bg-neutral-900 dark:bg-neutral-100 px-3 py-2 text-sm font-medium text-white dark:text-neutral-900 disabled:opacity-50"
            >
              {status === "sending" ? "发送中…" : "发送登录链接"}
            </button>
            {status === "error" && (
              <p className="text-sm text-red-600 dark:text-red-400">出错了:{errMsg}</p>
            )}
          </form>
        )}
      </div>
    </div>
  );
}
