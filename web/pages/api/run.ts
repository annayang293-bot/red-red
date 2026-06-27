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
import { resolveCaller } from "@/lib/auth";

const GITHUB_API = "https://api.github.com";
// Hardcoded for this project (Anna 2026-05-31): single repo, single workflow file. Keeps the
// API surface focused — there's no scenario where we'd dispatch a different repo / workflow
// from this endpoint.
const REPO_OWNER = "annayang293-bot";
const REPO_NAME = "red-red";
const WORKFLOW_FILE = "on-demand-run.yml";
const WORKFLOW_REF = "main";

// Topic allowlist — defense-in-depth alongside the workflow's env-indirection fix
// (Rex Phase 1, 2026-05-31). The workflow no longer interpolates `${{ inputs.topic }}` into
// a shell `run:` block, so injection is already neutralized at the YAML layer. We still reject
// obviously hostile / non-topic-looking inputs at the API layer: only Unicode letters/digits,
// whitespace, and `_ -` survive. That covers "AI 创业", "AI startup", "indie-hackers" etc. while
// rejecting `;`, backticks, quotes, `$`, `|`, shell expansion characters, etc.
const TOPIC_ALLOWLIST = /^[\p{L}\p{N}\p{Zs}_\-]{1,80}$/u;

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["POST"])) return;

  // AuthZ gate (Phase 3-4, the BYOK "hard gate"): this endpoint dispatches a PAID Apify run on a
  // workspace's token, so it must NOT be publicly triggerable. Verify the caller and resolve THEIR
  // workspace; we stamp that workspace_id onto the dispatch so the runner uses the right token and
  // the run is attributed to the right workspace (no more NULL-workspace manual runs). The runner
  // trusts whatever workspace_id it's handed — so the trust boundary is HERE: we only ever pass the
  // caller's own workspace, never a client-supplied one.
  const caller = await resolveCaller(req);
  if (!caller) return res.status(401).json({ error: "unauthorized" });

  const topic = String(req.body?.topic ?? "").trim();
  if (!topic) return res.status(400).json({ error: "missing_topic" });
  if (!TOPIC_ALLOWLIST.test(topic)) {
    return res.status(400).json({
      error: "invalid_topic",
      message:
        "Topic must be 1–80 chars of letters, digits, spaces, '_', or '-' only.",
    });
  }

  // No re-run throttle (Anna 2026-06-27): the old 6h lock guarded a shared project token against
  // burst manual re-runs. With BYOK each workspace runs on its OWN Apify token + can opt out of the
  // daily auto-run, so users bear their own cost and may re-run freely. (The daily cron keeps its
  // own separate double-fire dedup in /api/cron/daily.)
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
        body: JSON.stringify({
          ref: WORKFLOW_REF,
          inputs: { topic, workspace_id: caller.workspaceId },
        }),
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
