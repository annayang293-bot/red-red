/** POST /api/run { topic } — dispatch a GitHub Actions workflow_run for the pipeline.
 *
 *  Architecture change (A plan, 2026-05-31): on Vercel we can't spawn long-running Python
 *  subprocesses (Hobby Fluid budget is too tight + Vercel IPs get throttled by Reddit's
 *  anti-bot). Instead, this endpoint hands off to a GitHub Actions workflow that runs
 *  `python -m pipeline.run_once <topic>` on a GitHub-hosted runner (residential-IP class,
 *  not throttled by Reddit; runs 30–90s; writes the result to Supabase).
 *
 *  Contract change: this used to wait for the pipeline to finish and return the run summary
 *  inline. It now returns immediately after the dispatch is accepted by GitHub (HTTP 204);
 *  the UI polls `/api/runs/latest-id` to detect when the new run lands in Supabase, then
 *  loads the report. Frontend timeout: ~120s.
 */
import type { NextApiRequest, NextApiResponse } from "next";
import { ensureMethod, failError } from "@/lib/api";

const GITHUB_API = "https://api.github.com";
// Hardcoded for this project (Anna 2026-05-31): single repo, single workflow file. Keeps the
// API surface focused — there's no scenario where we'd dispatch a different repo / workflow
// from this endpoint.
const REPO_OWNER = "annayang293-bot";
const REPO_NAME = "red-red";
const WORKFLOW_FILE = "on-demand-run.yml";
const WORKFLOW_REF = "main";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["POST"])) return;
  const topic = String(req.body?.topic ?? "").trim();
  if (!topic) return res.status(400).json({ error: "missing_topic" });

  const pat = process.env.GITHUB_PAT;
  if (!pat) {
    return res
      .status(500)
      .json({ error: "missing_github_pat", message: "GITHUB_PAT env var not set on Vercel." });
  }

  try {
    // POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches
    // Success = HTTP 204 No Content. Anything else is a failure (most often 401 wrong PAT scope,
    // 404 wrong workflow filename, or 422 ref/input mismatch).
    const ghRes = await fetch(
      `${GITHUB_API}/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${pat}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
          "User-Agent": "red-red-vercel-dispatch",
        },
        body: JSON.stringify({ ref: WORKFLOW_REF, inputs: { topic } }),
      }
    );

    if (ghRes.status === 204) {
      return res.status(200).json({ ok: true, dispatched: true, topic });
    }
    // GitHub returns a JSON body with `message` on error responses.
    const errBody = await ghRes.text();
    return res.status(502).json({
      error: "dispatch_failed",
      status: ghRes.status,
      message: errBody.slice(0, 500),
    });
  } catch (e) {
    failError(res, e);
  }
}
