/** /api/topics
 *  GET — list topics (active first).
 *  POST { keyword } — set the keyword as the **current topic** (hard switch): archive old active → enable/create target.
 *
 *  Hard-switch semantics (aligned with schema: at most 1 active topic at any time): archive current active,
 *  then enable target. If the target keyword already exists (archived) → reuse + re-enable; otherwise create.
 *  Each topic's history (runs) is preserved. */
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
        .order("status", { ascending: true }) // active < archived (alphabetical)
        .order("started_at", { ascending: false });
      if (error) throw error;
      return res.status(200).json({ topics: data ?? [] });
    } catch (e) {
      return failError(res, e);
    }
  }

  if (req.method === "DELETE") {
    // DELETE { topic_id }: calls the **delete_topic_cascade** RPC (single-transaction cascade:
    // topic + runs + reports + orphan posts + starred; non-orphan posts have run_id reassigned to
    // a different topic's run).
    const topicId = Number(req.body?.topic_id);
    if (!Number.isInteger(topicId) || topicId <= 0) {
      return res.status(400).json({ error: "bad_topic_id" });
    }
    try {
      const { data, error } = await sb.rpc("delete_topic_cascade", { p_topic_id: topicId });
      if (error) {
        // Translate the RPC's RAISE EXCEPTION codes into friendly HTTP statuses.
        const msg = error.message || "";
        if (msg.includes("topic_not_found")) {
          return res.status(404).json({ error: "topic_not_found" });
        }
        if (msg.includes("cannot_delete_active_topic")) {
          return res.status(409).json({
            error: "cannot_delete_active",
            message: "The current topic cannot be deleted (switch to another topic first, then delete this one)",
          });
        }
        throw error;
      }
      return res.status(200).json(data);
    } catch (e) {
      return failError(res, e);
    }
  }

  // POST: hard switch to keyword — single-transaction RPC (archive old active + enable/create target;
  // atomic, never leaves 0 active).
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
