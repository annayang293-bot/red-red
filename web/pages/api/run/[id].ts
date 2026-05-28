/** GET /api/run/[id] — one run's report (report_top20 joined with posts_archive).
 *  id = "latest" → the most recent run; otherwise numeric run_id. */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { ReportRow, reportRowsToItems, buildReport } from "@/lib/report-mapping";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    const sb = getSupabaseAdmin();
    const idParam = String(req.query.id ?? "");

    // Resolve the target run
    let runQuery = sb.from("runs").select("run_id, topic_keyword, started_at, subreddits");
    if (idParam === "latest") {
      // "Most recent" = most recent run of the **current active topic** (otherwise switching to a
      // brand-new untouched topic would bleed an older report from another topic).
      const { data: actives, error: aErr } = await sb
        .from("topics").select("topic_id").eq("status", "active").limit(1);
      if (aErr) throw aErr;
      const activeId = (actives ?? [])[0]?.topic_id;
      if (!activeId) return res.status(200).json({ report: null });
      runQuery = runQuery.eq("topic_id", activeId).order("started_at", { ascending: false }).limit(1);
    } else {
      const runId = Number(idParam);
      if (!Number.isInteger(runId) || runId <= 0) {
        return res.status(400).json({ error: "bad_run_id" });
      }
      runQuery = runQuery.eq("run_id", runId).limit(1);
    }
    const { data: runRows, error: runErr } = await runQuery;
    if (runErr) throw runErr;
    const runRow = (runRows ?? [])[0];
    if (!runRow) return res.status(200).json({ report: null });

    const { data: rows, error: repErr } = await sb
      .from("report_top20")
      .select("rank, tier, comment, xhs_title, post_id, posts_archive(*)")
      .eq("run_id", runRow.run_id)
      .order("rank", { ascending: true });
    if (repErr) throw repErr;

    const items = reportRowsToItems((rows ?? []) as unknown as ReportRow[], runRow.run_id);
    const date = String(runRow.started_at ?? "").slice(0, 10);
    const report = buildReport(date, runRow.topic_keyword ?? "", items);
    // scrapedSubreddits = full fetched list (Anna 2026-05-27, topic-mapping visibility)
    const scrapedSubreddits: string[] = Array.isArray(runRow.subreddits)
      ? (runRow.subreddits as string[]).filter((s) => typeof s === "string")
      : [];
    res.status(200).json({ run_id: runRow.run_id, report, scrapedSubreddits });
  } catch (e) {
    failError(res, e);
  }
}
