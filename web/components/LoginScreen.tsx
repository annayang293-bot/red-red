/**
 * Magic-link login screen (System ① BYOK, Phase 0-B).
 *
 * Enter email → Supabase emails a one-time magic link → clicking it returns to this origin where
 * the browser client (detectSessionInUrl) consumes the tokens and the auth gate opens. No password.
 */
import { useState, FormEvent } from "react";
import { getSupabaseBrowser } from "@/lib/supabase-browser";

type Status = "idle" | "sending" | "sent" | "error";

export default function LoginScreen() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errMsg, setErrMsg] = useState("");

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
        <h1 className="text-lg font-semibold text-neutral-900 dark:text-neutral-100">
          登录
        </h1>
        <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
          输入邮箱,我们会发一个登录链接给你(无需密码)。
        </p>

        {status === "sent" ? (
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
