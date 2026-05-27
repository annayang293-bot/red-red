/** GET /api/run/[id] — 某次 run 的报告(report_top20 join posts_archive)。
 *  id = "latest" → 最近一次 run;否则按 run_id 数字。 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { ReportRow, reportRowsToItems, buildReport } from "@/lib/report-mapping";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    const sb = getSupabaseAdmin();
    const idParam = String(req.query.id ?? "");

    // 解析目标 run
    let runQuery = sb.from("runs").select("run_id, topic_keyword, started_at");
    if (idParam === "latest") {
      // "最近一次" = **当前 active topic** 的最近一次(否则切到没跑过的新主题会串到别题的旧报告)
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
    res.status(200).json({ run_id: runRow.run_id, report });
  } catch (e) {
    failError(res, e);
  }
}
