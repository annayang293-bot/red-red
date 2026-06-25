/** GET /api/run/[id] — one run's report (report_top20 joined with posts_archive).
 *  id = "latest" → the most recent run; otherwise numeric run_id. */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";
import { ReportRow, reportRowsToItems, buildReport } from "@/lib/report-mapping";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    // Phase 3: every report read is scoped to the caller's workspace. The numeric-id path filters
    // runs by workspace_id so a logged-in user can't read another workspace's run by guessing its id
    // (a cross-workspace IDOR); an out-of-workspace id just resolves to "no report".
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    const sb = getSupabaseAdmin();
    const idParam = String(req.query.id ?? "");

    // Resolve the target run (now also fetch topic_id so we can compute "recurring vs new")
    let runQuery = sb
      .from("runs")
      .select("run_id, topic_id, topic_keyword, started_at, subreddits")
      .eq("workspace_id", caller.workspaceId);
    if (idParam === "latest") {
      // "Most recent" = most recent run of THIS workspace's **current active topic** (otherwise
      // switching to a brand-new untouched topic would bleed an older report from another topic).
      const { data: actives, error: aErr } = await sb
        .from("topics")
        .select("topic_id")
        .eq("workspace_id", caller.workspaceId)
        .eq("status", "active")
        .limit(1);
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

    // "Recurring" semantics (Anna 2026-05-28): a post is recurring if it appeared in any EARLIER
    // same-topic run's Top-20 — NOT just "ever existed in posts_archive". So we fetch the set of
    // post_ids that showed up in report_top20 for runs of this topic with started_at < this run's,
    // and pass it down. Two-step query because PostgREST can't combine in one round trip while
    // filtering by the parent run's started_at.
    const earlierRunsQ = await sb
      .from("runs")
      .select("run_id")
      .eq("workspace_id", caller.workspaceId)
      .eq("topic_id", runRow.topic_id)
      .lt("started_at", runRow.started_at);
    if (earlierRunsQ.error) throw earlierRunsQ.error;
    const earlierRunIds = (earlierRunsQ.data ?? []).map((r) => r.run_id);
    const previouslyReportedPostIds = new Set<number>();
    if (earlierRunIds.length > 0) {
      const earlierReportQ = await sb
        .from("report_top20")
        .select("post_id")
        .in("run_id", earlierRunIds);
      if (earlierReportQ.error) throw earlierReportQ.error;
      for (const r of earlierReportQ.data ?? []) {
        if (typeof r.post_id === "number") previouslyReportedPostIds.add(r.post_id);
      }
    }

    const items = reportRowsToItems(
      (rows ?? []) as unknown as ReportRow[],
      previouslyReportedPostIds,
    );
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
