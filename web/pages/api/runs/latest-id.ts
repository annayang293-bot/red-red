/** GET /api/runs/latest-id — { run_id, started_at } | { run_id: null } for the currently-active topic.
 *
 *  Used by the client to poll for "has a new run landed yet" after dispatching a GH Actions
 *  workflow_run. The client remembers the run_id it had *before* dispatching, polls every ~5s,
 *  and considers the run done when the returned run_id is strictly greater than the baseline.
 *
 *  This is a deliberately tiny endpoint (no joins, single row) so it stays cheap to hit on
 *  a 5s loop. Reading the full report happens once, from `/api/run/[id]`, only after the
 *  baseline check fires.
 *
 *  Note on topic selection: aligned with `/api/run/latest` — "latest" means latest run of the
 *  active topic, not latest across all topics. Otherwise dispatching topic B while topic A is
 *  active would never satisfy the polling condition for an A-watcher.
 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    // Phase 3: scope to the caller's workspace — "latest run of the active topic" must mean THIS
    // workspace's active topic, never bleed another workspace's runs into the polling signal.
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    const sb = getSupabaseAdmin();

    const { data: actives, error: aErr } = await sb
      .from("topics")
      .select("topic_id")
      .eq("workspace_id", caller.workspaceId)
      .eq("status", "active")
      .limit(1);
    if (aErr) throw aErr;
    const activeId = (actives ?? [])[0]?.topic_id;
    if (!activeId) return res.status(200).json({ run_id: null });

    const { data: runs, error: rErr } = await sb
      .from("runs")
      .select("run_id, started_at")
      .eq("workspace_id", caller.workspaceId)
      .eq("topic_id", activeId)
      .order("started_at", { ascending: false })
      .limit(1);
    if (rErr) throw rErr;
    const latest = (runs ?? [])[0];
    if (!latest) return res.status(200).json({ run_id: null });

    // No-cache: this endpoint is the freshness signal for the polling loop; a stale cached value
    // would defeat the entire purpose.
    res.setHeader("Cache-Control", "no-store");
    res.status(200).json({ run_id: latest.run_id, started_at: latest.started_at });
  } catch (e) {
    failError(res, e);
  }
}
