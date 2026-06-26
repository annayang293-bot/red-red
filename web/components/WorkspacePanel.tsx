/** ⚙️ 设置 → 工作区卡片 (Phase 3 sharing).
 *  Switch between the workspaces you belong to, see members, and (owner only) invite/remove members
 *  by email. Talks to /api/workspaces (list+members) and /api/workspaces/members (invite/remove)
 *  via authedFetch; the switcher writes the active workspace to localStorage (CURRENT_WS_KEY) and
 *  reloads so all data refetches under the chosen workspace. */
import { useCallback, useEffect, useState, FormEvent } from "react";
import { authedFetch, CURRENT_WS_KEY } from "@/lib/authed-fetch";

interface WorkspaceLite {
  id: string;
  name: string;
  role: string;
}
interface MemberLite {
  user_id: string;
  email: string | null;
  role: string;
  is_self: boolean;
}
interface WsData {
  current: string;
  role: string;
  workspaces: WorkspaceLite[];
  members: MemberLite[];
}

const INVITE_ERR: Record<string, string> = {
  bad_email: "邮箱格式不对。",
  user_not_found: "没找到这个邮箱的账号 —— 让对方先用 Google 登录一次,再来邀请。",
  cannot_invite_self: "不能邀请你自己。",
  owner_only: "只有工作区拥有者能邀请成员。",
  unauthorized: "登录已过期,请刷新。",
};

export default function WorkspacePanel() {
  const [data, setData] = useState<WsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await authedFetch("/api/workspaces");
      const j = await r.json();
      if (!r.ok) {
        setErr(INVITE_ERR[j?.error] || "加载失败,请刷新。");
        setData(null);
      } else {
        setErr("");
        setData(j);
      }
    } catch {
      setErr("加载失败,请刷新。");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    // async IIFE so setState happens after await (not synchronously in the effect body).
    (async () => {
      await load();
    })();
  }, [load]);

  function switchTo(id: string) {
    if (!data || id === data.current) return;
    window.localStorage.setItem(CURRENT_WS_KEY, id);
    window.location.reload(); // refetch everything under the chosen workspace
  }

  async function invite(e: FormEvent) {
    e.preventDefault();
    const v = email.trim();
    if (!v) return;
    setBusy(true);
    setNotice("");
    setErr("");
    try {
      const r = await authedFetch("/api/workspaces/members", {
        method: "POST",
        body: JSON.stringify({ email: v }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErr(INVITE_ERR[j?.error] || "邀请失败,请重试。");
      } else {
        setNotice(j.already ? `${v} 已经是成员了。` : `已邀请 ${v}。`);
        setEmail("");
        await load();
      }
    } catch {
      setErr("邀请失败,请重试。");
    }
    setBusy(false);
  }

  async function remove(userId: string, label: string) {
    if (!window.confirm(`把 ${label} 移出这个工作区?(对方将不再能看到这里的报告)`)) return;
    setBusy(true);
    setErr("");
    try {
      const r = await authedFetch("/api/workspaces/members", {
        method: "DELETE",
        body: JSON.stringify({ user_id: userId }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setErr(INVITE_ERR[j?.error] || "移除失败,请重试。");
      } else {
        await load();
      }
    } catch {
      setErr("移除失败,请重试。");
    }
    setBusy(false);
  }

  const isOwner = data?.role === "owner";

  return (
    <div className="mb-5 max-w-xl rounded-xl border border-line bg-panel p-5">
      <div className="text-sm font-semibold text-ink">工作区 / 成员</div>
      <p className="mt-1 text-[13px] text-mut">
        一个工作区里的成员共享同一份报告、收藏和话题。拥有者可以邀请别的邮箱进来一起看。
      </p>

      {err && (
        <p className="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-[13px] text-red-700">
          {err}
        </p>
      )}
      {notice && (
        <p className="mt-3 rounded-lg bg-terrasoft px-3 py-2 text-[13px] text-ink">{notice}</p>
      )}

      {loading ? (
        <p className="mt-4 text-[13px] text-mut">加载中…</p>
      ) : data ? (
        <div className="mt-4 space-y-4">
          {data.workspaces.length > 1 && (
            <div>
              <label className="mb-1 block text-[12px] text-mut">当前工作区</label>
              <select
                value={data.current}
                onChange={(e) => switchTo(e.target.value)}
                className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-terra"
              >
                {data.workspaces.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                    {w.role === "owner" ? "(我的)" : "(受邀)"}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <div className="mb-1 text-[12px] text-mut">成员</div>
            <div className="space-y-1.5">
              {data.members.map((m) => (
                <div
                  key={m.user_id}
                  className="flex items-center justify-between rounded-lg bg-white px-3 py-2 text-[13px] text-ink"
                >
                  <span className="truncate">
                    {m.email ?? m.user_id.slice(0, 8)}
                    {m.is_self && " (你)"}
                    {m.role === "owner" && (
                      <span className="ml-2 rounded-full bg-terrasoft px-2 py-0.5 text-[11px] text-terra">
                        拥有者
                      </span>
                    )}
                  </span>
                  {isOwner && !m.is_self && m.role !== "owner" && (
                    <button
                      type="button"
                      onClick={() => remove(m.user_id, m.email ?? m.user_id.slice(0, 8))}
                      disabled={busy}
                      className="ml-2 shrink-0 text-[12px] text-mut hover:text-[#c2562a] disabled:opacity-40"
                    >
                      移除
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          {isOwner && (
            <form onSubmit={invite} className="space-y-2">
              <label className="block text-[12px] text-mut">邀请成员(填对方邮箱)</label>
              <div className="flex gap-2">
                <input
                  type="email"
                  autoComplete="off"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@example.com"
                  className="min-w-0 flex-1 rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-terra"
                />
                <button
                  type="submit"
                  disabled={busy || !email.trim()}
                  className="shrink-0 rounded-lg bg-terra px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {busy ? "…" : "邀请"}
                </button>
              </div>
              <p className="text-[11px] text-mut">
                对方需要先用 Google 登录过一次(这样账号才存在),你才能邀请到。
              </p>
            </form>
          )}
        </div>
      ) : null}
    </div>
  );
}
