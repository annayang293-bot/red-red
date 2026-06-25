/** GET /api/runs — summary of the most recent N runs (for the history list / picker). */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    // Phase 3: history is scoped to the caller's workspace.
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    const limit = Math.min(Number(req.query.limit) || 30, 100);
    const { data, error } = await getSupabaseAdmin()
      .from("runs")
      .select(
        "run_id, topic_keyword, triggered_by, status, started_at, posts_count, top20_count, ai_mode, sanity_status"
      )
      .eq("workspace_id", caller.workspaceId)
      .order("started_at", { ascending: false })
      .limit(limit);
    if (error) throw error;
    res.status(200).json({ runs: data ?? [] });
  } catch (e) {
    failError(res, e);
  }
}
