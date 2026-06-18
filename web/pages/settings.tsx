/** /settings — manage this workspace's Apify token (BYOK Phase 1-C).
 *
 * Talks to /api/apify-token (GET status / POST save / DELETE), attaching the Supabase access token
 * as a Bearer header. The endpoint validates + encrypts; this page never sees ciphertext, and only
 * ever shows the last 6 chars + Apify username. Error responses are codes — mapped to text here.
 */
import { useEffect, useState, FormEvent } from "react";
import Link from "next/link";
import { getSupabaseBrowser } from "@/lib/supabase-browser";

interface TokenStatus {
  configured: boolean;
  last6: string | null;
  username: string | null;
  validated_at: string | null;
}

const ERR: Record<string, string> = {
  invalid_token: "这个 Apify token 无效或没有权限(Apify 没认出它)。",
  apify_unreachable: "连不上 Apify,请稍后再试(不是你的 token 的问题)。",
  bad_token_format: "token 格式看起来不对(长度异常)。",
  missing_token: "请先填入 token。",
  store_failed: "保存失败,请重试。",
  delete_failed: "删除失败,请重试。",
  server_error: "服务器出错,请重试。",
  unauthorized: "登录已过期,请重新登录。",
};
const errText = (code?: string) => (code && ERR[code]) || "出错了,请重试。";

/** fetch with the current Supabase access token as a Bearer header. */
async function authedFetch(input: string, init?: RequestInit) {
  const {
    data: { session },
  } = await getSupabaseBrowser().auth.getSession();
  const headers = new Headers(init?.headers);
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  headers.set("Content-Type", "application/json");
  return fetch(input, { ...init, headers });
}

export default function SettingsPage() {
  const [status, setStatus] = useState<TokenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [reloadKey, setReloadKey] = useState(0); // bump to re-fetch (after save/delete)

  // Fetch status on mount + whenever reloadKey bumps. setState lives in the async IIFE's
  // post-await continuation (not a synchronous setState-in-effect), guarded by `cancelled`.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await authedFetch("/api/apify-token");
        const j = await r.json();
        if (cancelled) return;
        if (!r.ok) {
          setErr(errText(j?.error));
          setStatus(null);
        } else {
          setErr("");
          setStatus(j);
          setEditing(!j.configured);
        }
      } catch {
        if (!cancelled) setErr("加载失败,请刷新。");
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const r = await authedFetch("/api/apify-token", {
        method: "POST",
        body: JSON.stringify({ token: token.trim() }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErr(errText(j?.error));
      } else {
        setToken("");
        setEditing(false);
        setReloadKey((k) => k + 1);
      }
    } catch {
      setErr("保存失败,请重试。");
    }
    setBusy(false);
  }

  async function remove() {
    if (!window.confirm("确定删除这个 Apify token 吗?删除后这个工作区将无法跑抓取,直到重新填入。")) {
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const r = await authedFetch("/api/apify-token", { method: "DELETE" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setErr(errText(j?.error));
      } else {
        setReloadKey((k) => k + 1);
      }
    } catch {
      setErr("删除失败,请重试。");
    }
    setBusy(false);
  }

  return (
    <div className="min-h-screen bg-neutral-50 dark:bg-neutral-950 px-4 py-10">
      <div className="mx-auto w-full max-w-lg">
        <Link
          href="/"
          className="text-sm text-neutral-500 dark:text-neutral-400 hover:underline"
        >
          ← 返回
        </Link>

        <div className="mt-4 rounded-2xl border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-8 shadow-sm">
          <h1 className="text-lg font-semibold text-neutral-900 dark:text-neutral-100">
            Apify Token
          </h1>
          <p className="mt-1 text-sm text-neutral-500 dark:text-neutral-400">
            用你自己的 Apify token 跑抓取 —— 用你自己的额度。token 会加密保存,我们只显示后 6 位。
          </p>

          {err && (
            <p className="mt-4 rounded-lg bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 px-3 py-2 text-sm text-red-700 dark:text-red-300">
              {err}
            </p>
          )}

          {loading ? (
            <p className="mt-6 text-sm text-neutral-400">加载中…</p>
          ) : status?.configured && !editing ? (
            <div className="mt-6 space-y-4">
              <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-900 p-4 text-sm text-emerald-800 dark:text-emerald-300">
                ✅ 已配置
                {status.username && <span> · Apify 账号 <b>{status.username}</b></span>}
                <span> · token <code>…{status.last6}</code></span>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="rounded-lg border border-neutral-300 dark:border-neutral-700 px-3 py-2 text-sm text-neutral-700 dark:text-neutral-200"
                >
                  更换
                </button>
                <button
                  type="button"
                  onClick={remove}
                  disabled={busy}
                  className="rounded-lg border border-red-300 dark:border-red-800 px-3 py-2 text-sm text-red-600 dark:text-red-400 disabled:opacity-50"
                >
                  删除
                </button>
              </div>
            </div>
          ) : (
            <form onSubmit={save} className="mt-6 space-y-3">
              <input
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="apify_api_..."
                className="w-full rounded-lg border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-950 px-3 py-2 text-sm text-neutral-900 dark:text-neutral-100 outline-none focus:ring-2 focus:ring-neutral-900 dark:focus:ring-neutral-300"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={busy || !token.trim()}
                  className="rounded-lg bg-neutral-900 dark:bg-neutral-100 px-3 py-2 text-sm font-medium text-white dark:text-neutral-900 disabled:opacity-50"
                >
                  {busy ? "验证中…" : "验证并保存"}
                </button>
                {status?.configured && (
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(false);
                      setToken("");
                      setErr("");
                    }}
                    className="rounded-lg border border-neutral-300 dark:border-neutral-700 px-3 py-2 text-sm text-neutral-600 dark:text-neutral-300"
                  >
                    取消
                  </button>
                )}
              </div>
            </form>
          )}

          <details className="mt-6 text-sm text-neutral-500 dark:text-neutral-400">
            <summary className="cursor-pointer text-neutral-600 dark:text-neutral-300">
              怎么创建一个「受限」token(更安全,推荐)?
            </summary>
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>登录 Apify → 右上头像 → <b>Settings → API &amp; Integrations</b>。</li>
              <li>点 <b>Create a new token</b>,打开 <b>Limit token permissions</b>。</li>
              <li>只授予「运行指定 Actor」的权限,选我们用的 Reddit 爬虫。</li>
              <li>复制 token 粘到上面。这样即使泄露,别人也只能跑这个爬虫,读不了你账号的其它东西。</li>
            </ol>
          </details>
        </div>
      </div>
    </div>
  );
}
