/** GET /api/cron/daily — Vercel Cron entrypoint for the daily pipeline run.
 *
 * Why this exists (2026-06-15): GitHub Actions `schedule:` is best-effort and routinely delayed
 * or dropped under load — the 16:00 UTC daily cron silently never fired on 2026-06-15. Vercel Cron
 * is reliable, so we move the *trigger* here. The pipeline itself still runs on GitHub Actions
 * (Vercel can't run the long Python job and its IPs get Reddit-throttled): this endpoint just fires
 * a workflow_dispatch on `cron-daily.yml`; the GH runner does the real work and writes to Supabase.
 * Configured in `web/vercel.json` under `crons`.
 *
 * Auth: Vercel attaches `Authorization: Bearer $CRON_SECRET` to cron invocations when the
 * CRON_SECRET env var is set. We REQUIRE it to match — this endpoint kicks off a paid (~$0.72)
 * Apify run, so it must not be publicly triggerable. If CRON_SECRET is unset we fail closed (401).
 */
import type { NextApiRequest, NextApiResponse } from "next";
import { dispatchWorkflow } from "@/lib/gh-dispatch";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "method_not_allowed" });
  }

  // Fail closed: require CRON_SECRET to be configured AND to match the header Vercel sends.
  const secret = process.env.CRON_SECRET;
  if (!secret || req.headers.authorization !== `Bearer ${secret}`) {
    return res.status(401).json({ error: "unauthorized" });
  }

  // cron-daily.yml takes no inputs (topic is hard-coded in the workflow, triggered_by=cron).
  const result = await dispatchWorkflow("cron-daily.yml", {});
  if (!result.dispatched) {
    return res.status(502).json({ ok: false, error: "dispatch_failed", ...result });
  }
  return res.status(200).json({ ok: true, dispatched: true, workflow: "cron-daily.yml" });
}
