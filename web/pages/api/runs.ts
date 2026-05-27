/** GET /api/runs — 最近若干次 run 的概要(供历史列表/选择)。 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    const limit = Math.min(Number(req.query.limit) || 30, 100);
    const { data, error } = await getSupabaseAdmin()
      .from("runs")
      .select(
        "run_id, topic_keyword, triggered_by, status, started_at, posts_count, top20_count, ai_mode, sanity_status"
      )
      .order("started_at", { ascending: false })
      .limit(limit);
    if (error) throw error;
    res.status(200).json({ runs: data ?? [] });
  } catch (e) {
    failError(res, e);
  }
}
