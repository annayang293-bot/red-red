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
 *
 * The GitHub dispatch is inlined (mirrors /api/run.ts) on purpose: the shared dispatchWorkflow()
 * helper lives in lib/gh-dispatch.ts which belongs to the not-yet-shipped System ②, so importing
 * it would couple this System ① endpoint to unshipped code.
 */
import type { NextApiRequest, NextApiResponse } from "next";

const GITHUB_API = "https://api.github.com";
const REPO_OWNER = "annayang293-bot";
const REPO_NAME = "red-red";
const WORKFLOW_FILE = "cron-daily.yml"; // dispatch-only; topic hard-coded inside, triggered_by=cron
const WORKFLOW_REF = "main";

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

  const pat = process.env.GITHUB_PAT;
  if (!pat) {
    return res.status(500).json({ error: "missing_github_pat" });
  }

  try {
    // cron-daily.yml defines no inputs, so the dispatch body carries only the ref.
    const ghRes = await fetch(
      `${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${pat}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "red-red-vercel-cron",
        },
        body: JSON.stringify({ ref: WORKFLOW_REF }),
      }
    );

    if (ghRes.status === 204) {
      return res.status(200).json({ ok: true, dispatched: true, workflow: WORKFLOW_FILE });
    }
    const errBody = await ghRes.text();
    return res.status(502).json({
      error: "dispatch_failed",
      status: ghRes.status,
      message: errBody.slice(0, 300),
    });
  } catch (e) {
    return res
      .status(500)
      .json({ error: "dispatch_exception", message: e instanceof Error ? e.message : String(e) });
  }
}
