/** /api/topics
 *  GET  — 主题列表(active 在前)。
 *  POST { keyword } — 把该 keyword 设为**当前主题**(硬切换):旧 active 归档 → 目标启用/新建。
 *
 *  硬切换语义(对齐 schema:同一时刻最多 1 个 active topic):先归档当前 active,再启用目标。
 *  目标 keyword 已存在(归档态)→ 复用并重新启用;不存在 → 新建。各主题历史(runs)各自保留。 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET", "POST", "DELETE"])) return;
  const sb = getSupabaseAdmin();

  if (req.method === "GET") {
    try {
      const { data, error } = await sb
        .from("topics")
        .select("topic_id, keyword, status, started_at")
        .order("status", { ascending: true }) // active < archived(字母序)
        .order("started_at", { ascending: false });
      if (error) throw error;
      return res.status(200).json({ topics: data ?? [] });
    } catch (e) {
      return failError(res, e);
    }
  }

  if (req.method === "DELETE") {
    // DELETE { topic_id }:走 **delete_topic_cascade** RPC(单事务级联:topic + runs + reports +
    // 孤立 posts + starred;非孤立 posts 转移 run_id 到其它主题的 run)。
    const topicId = Number(req.body?.topic_id);
    if (!Number.isInteger(topicId) || topicId <= 0) {
      return res.status(400).json({ error: "bad_topic_id" });
    }
    try {
      const { data, error } = await sb.rpc("delete_topic_cascade", { p_topic_id: topicId });
      if (error) {
        // 把 RPC 里 RAISE EXCEPTION 的错码翻成友好状态
        const msg = error.message || "";
        if (msg.includes("topic_not_found")) {
          return res.status(404).json({ error: "topic_not_found" });
        }
        if (msg.includes("cannot_delete_active_topic")) {
          return res.status(409).json({
            error: "cannot_delete_active",
            message: "当前主题不能删(先切到别的主题再删它)",
          });
        }
        throw error;
      }
      return res.status(200).json(data);
    } catch (e) {
      return failError(res, e);
    }
  }

  // POST:硬切换到 keyword —— 走单事务 RPC(归档旧 active + 启用/新建目标,原子,不会留 0 active)
  const keyword = String(req.body?.keyword ?? "").trim();
  if (!keyword) return res.status(400).json({ error: "missing_keyword" });
  try {
    const { data, error } = await sb.rpc("switch_active_topic", { p_keyword: keyword });
    if (error) throw error;
    res.status(200).json({ ok: true, topic: data });
  } catch (e) {
    failError(res, e);
  }
}
