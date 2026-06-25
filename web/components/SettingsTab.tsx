/** ⚙️ 设置 tab — currently houses the BYOK Apify-token vault (Phase 1-C).
 *  Talks to /api/apify-token with the Supabase access token as a Bearer header; never sees
 *  ciphertext (only last6 + username). Error responses are codes, mapped to text here. */
import { useEffect, useState, FormEvent } from "react";
import { useT } from "@/lib/i18n";
import { authedFetch } from "@/lib/authed-fetch";

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

export default function SettingsTab() {
  const { t } = useT();
  const [status, setStatus] = useState<TokenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

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
        setLoading(true);
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
        setLoading(true);
        setReloadKey((k) => k + 1);
      }
    } catch {
      setErr("删除失败,请重试。");
    }
    setBusy(false);
  }

  return (
    <div>
      <h1 className="text-xl font-bold">{t("set.heading")}</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">{t("set.subtitle")}</p>

      <div className="max-w-xl rounded-xl border border-line bg-panel p-5">
        <div className="text-sm font-semibold text-ink">Apify Token</div>
        <p className="mt-1 text-[13px] text-mut">
          用你自己的 Apify token 跑抓取 —— 用你自己的额度。token 会加密保存,我们只显示后 6 位。
        </p>

        {err && (
          <p className="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-[13px] text-red-700">
            {err}
          </p>
        )}

        {loading ? (
          <p className="mt-4 text-[13px] text-mut">加载中…</p>
        ) : status?.configured && !editing ? (
          <div className="mt-4 space-y-3">
            <div className="rounded-lg bg-terrasoft px-3 py-2.5 text-[13px] text-ink">
              ✅ 已配置
              {status.username && <span> · Apify 账号 <b>{status.username}</b></span>}
              <span> · token <code>…{status.last6}</code></span>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="rounded-lg border border-line px-3 py-2 text-sm text-ink hover:bg-terrasoft"
              >
                更换
              </button>
              <button
                type="button"
                onClick={remove}
                disabled={busy}
                className="rounded-lg border border-red-300 px-3 py-2 text-sm text-red-600 disabled:opacity-50"
              >
                删除
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={save} className="mt-4 space-y-3">
            <input
              type="password"
              autoComplete="off"
              aria-label="Apify token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="apify_api_..."
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-terra"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy || !token.trim()}
                className="rounded-lg bg-terra px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
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
                  className="rounded-lg border border-line px-3 py-2 text-sm text-mut hover:bg-terrasoft"
                >
                  取消
                </button>
              )}
            </div>
          </form>
        )}

        <details className="mt-5 text-[13px] text-mut">
          <summary className="cursor-pointer text-ink/80">怎么创建一个「受限」token(更安全,推荐)?</summary>
          <p className="mt-2">
            受限 token 万一泄露,别人也只能跑我们这个 Reddit 爬虫,碰不到你账号里的其它东西。
          </p>
          <ol className="mt-2 list-decimal space-y-1.5 pl-5">
            <li>
              打开{" "}
              <a
                href="https://console.apify.com/settings/api-integrations"
                target="_blank"
                rel="noreferrer"
                className="text-terra underline"
              >
                console.apify.com/settings/api-integrations
              </a>
              (用你的 Apify 账号登录)。
            </li>
            <li>点 <b>Create a new token</b>,随便起个名(比如 <code>xhs-reddit</code>)。</li>
            <li>打开 <b>Limit token permissions</b> 开关。</li>
            <li>
              在 Actor 权限里搜索并选 <code>harshmaur/reddit-scraper</code> —— 就是我们抓 Reddit 用的那个
              (可点{" "}
              <a
                href="https://apify.com/harshmaur/reddit-scraper"
                target="_blank"
                rel="noreferrer"
                className="text-terra underline"
              >
                apify.com/harshmaur/reddit-scraper
              </a>{" "}
              确认),勾上 <b>Run</b> 权限。
            </li>
            <li>创建后复制 token,粘到上面的输入框。</li>
          </ol>
          <p className="mt-2">
            嫌麻烦?直接用你的<b>默认 token</b> 也行 —— 功能完全一样,只是没限制范围。
          </p>
        </details>
      </div>
    </div>
  );
}
