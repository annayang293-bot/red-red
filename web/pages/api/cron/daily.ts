/** GET /api/cron/daily — Vercel Cron entrypoint for the daily pipeline runs.
 *
 * Phase 3-7 (2026-06-25): the daily run is now a per-topic **auto_daily** opt-in feature, not a
 * single hardcoded job. This endpoint enumerates every topic with auto_daily=true (and a real
 * workspace) and dispatches `cron-daily.yml` once per (topic, workspace) — so each runs on THAT
 * workspace's own Apify token (BYOK) and writes that workspace's reports. The runs are stamped with
 * the workspace, so the per-workspace scoped report reads (Phase 3-5) can see them.
 *
 * Why this exists (2026-06-15): GitHub Actions `schedule:` is best-effort and routinely delayed or
 * dropped under load — the 16:00 UTC daily cron silently never fired on 2026-06-15. Vercel Cron is
 * reliable, so the *trigger* lives here. The pipeline itself still runs on GitHub Actions (Vercel
 * can't run the long Python job and its IPs get Reddit-throttled): this endpoint just fires
 * workflow_dispatch on `cron-daily.yml`; the GH runner does the real work and writes to Supabase.
 * Configured in `web/vercel.json` under `crons`.
 *
 * Auth: Vercel attaches `Authorization: Bearer $CRON_SECRET` to cron invocations when the
 * CRON_SECRET env var is set. We REQUIRE it to match — this endpoint kicks off paid (~$0.72 each)
 * Apify runs, so it must not be publicly triggerable. If CRON_SECRET is unset we fail closed (401).
 *
 * Cost guard: skip a topic that already has a run within RECENT_RUN_HOURS — a defensive dedupe so a
 * double cron-fire (Vercel retry, manual + scheduled overlap) can't double-bill a workspace's Apify
 * account. The daily cadence is 24h, so a 20h window never blocks the next legitimate day.
 *
 * The GitHub dispatch is inlined (mirrors /api/run.ts) on purpose: the shared dispatchWorkflow()
 * helper lives in lib/gh-dispatch.ts which belongs to the not-yet-shipped System ②, so importing it
 * would couple this System ① endpoint to unshipped code.
 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";

const GITHUB_API = "https://api.github.com";
const REPO_OWNER = "annayang293-bot";
const REPO_NAME = "red-red";
const WORKFLOW_FILE = "cron-daily.yml"; // now takes (topic, workspace_id) inputs; triggered_by=cron
const WORKFLOW_REF = "main";
const RECENT_RUN_HOURS = 20;

interface AutoTopic {
  topic_id: number;
  keyword: string;
  workspace_id: string;
}

async function dispatchOne(pat: string, topic: string, workspaceId: string): Promise<boolean> {
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
      body: JSON.stringify({
        ref: WORKFLOW_REF,
        inputs: { topic, workspace_id: workspaceId },
      }),
    },
  );
  if (ghRes.status === 204) return true;
  const body = await ghRes.text();
  console.error(
    `[cron/daily] dispatch failed for "${topic}" (ws ${workspaceId.slice(0, 8)}…): ${ghRes.status} ${body.slice(0, 200)}`,
  );
  return false;
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  // DEPLOY ORDER (Phase 3-7): push `.github/workflows/cron-daily.yml` to GitHub BEFORE running
  // `vercel --prod`. This endpoint dispatches WITH inputs; the GitHub REST API returns 422 if inputs
  // are sent to a workflow that hasn't declared them — so a Vercel deploy ahead of the GitHub push
  // would make every nightly tick 422 (dispatched:0) until the workflow lands.
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
    const sb = getSupabaseAdmin();

    // Every topic opted into the daily auto-run. workspace_id must be present (a NULL-workspace
    // topic can't be attributed to a token / report view, so it's not eligible).
    const { data: topics, error: tErr } = await sb
      .from("topics")
      .select("topic_id, keyword, workspace_id")
      .eq("auto_daily", true)
      .not("workspace_id", "is", null);
    if (tErr) {
      console.error("[cron/daily] topics query error:", tErr.message);
      return res.status(500).json({ error: "topics_query_failed" });
    }

    const autoTopics = (topics ?? []) as AutoTopic[];
    if (autoTopics.length === 0) {
      return res.status(200).json({ ok: true, dispatched: 0, skipped: 0, topics: 0 });
    }

    // Cost guard: drop topics that already ran within RECENT_RUN_HOURS for their workspace.
    const sinceIso = new Date(Date.now() - RECENT_RUN_HOURS * 3.6e6).toISOString();
    const results = { dispatched: 0, skipped: 0, failed: 0 };
    const detail: Array<{ topic: string; status: string }> = [];

    for (const t of autoTopics) {
      const { data: recent, error: rErr } = await sb
        .from("runs")
        .select("run_id")
        .eq("workspace_id", t.workspace_id)
        .eq("topic_keyword", t.keyword)
        .gte("started_at", sinceIso)
        .limit(1);
      // On a guard-query error, fail OPEN toward NOT dispatching — a missed daily run is cheaper and
      // safer than a possible double-bill. (Distinct from /api/run, where a user explicitly clicked.)
      if (rErr) {
        console.error(`[cron/daily] recency check failed for "${t.keyword}":`, rErr.message);
        results.skipped++;
        detail.push({ topic: t.keyword, status: "guard_error_skipped" });
        continue;
      }
      if ((recent ?? []).length > 0) {
        results.skipped++;
        detail.push({ topic: t.keyword, status: "ran_recently" });
        continue;
      }
      const ok = await dispatchOne(pat, t.keyword, t.workspace_id);
      if (ok) {
        results.dispatched++;
        detail.push({ topic: t.keyword, status: "dispatched" });
      } else {
        results.failed++;
        detail.push({ topic: t.keyword, status: "dispatch_failed" });
      }
    }

    // Surface a "nothing got out the door" night to the Vercel Cron dashboard (which only sees the
    // HTTP status): if we ATTEMPTED dispatches and none succeeded, return 500. A partial success
    // (some dispatched, some failed) or an all-skipped night (everything ran recently) is a healthy
    // 200 — the body carries the full breakdown either way.
    const nothingDispatched = results.failed > 0 && results.dispatched === 0;
    return res
      .status(nothingDispatched ? 500 : 200)
      .json({ ok: !nothingDispatched, topics: autoTopics.length, ...results, detail });
  } catch (e) {
    return res
      .status(500)
      .json({ error: "dispatch_exception", message: e instanceof Error ? e.message : String(e) });
  }
}
