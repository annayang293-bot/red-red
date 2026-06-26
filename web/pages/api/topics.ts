/** /api/topics — per-workspace topic management (Phase 3-6, model B). All methods are auth-gated
 *  (resolveCaller → the caller's workspace) and scoped to that workspace.
 *
 *  GET    — list this workspace's topics (active first).
 *  POST   { keyword, hint? } — switch/add: make `keyword` this workspace's active (currently-viewed)
 *           topic via switch_workspace_topic (archives the old active, re-activates or inserts the
 *           target). Optional mapping_hint persisted on the topic.
 *  PATCH  { topic_id, auto_daily } — toggle a topic's daily auto-run opt-in.
 *  DELETE { topic_id } — delete a topic + its history (delete_topic_cascade), after verifying it
 *           belongs to the caller's workspace.
 *
 *  Why the RPCs are safe here: switch_workspace_topic / delete_topic_cascade are service-key-only
 *  (EXECUTE revoked from anon/authenticated) and have no internal membership check. This route is the
 *  membership gate: it resolves the caller's workspace from the verified JWT and only ever passes
 *  that workspace_id (POST) or a topic_id it has confirmed belongs to that workspace (DELETE). */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";

// Same allowlist as /api/run: a topic keyword becomes a workflow_dispatch input downstream, so keep
// it to Unicode letters/digits, whitespace, and `_ -` (rejects shell-meaningful characters).
const TOPIC_ALLOWLIST = /^[\p{L}\p{N}\p{Zs}_\-]{1,80}$/u;

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET", "POST", "PATCH", "DELETE"])) return;

  const caller = await resolveCaller(req);
  if (!caller) return res.status(401).json({ error: "unauthorized" });
  const sb = getSupabaseAdmin();

  if (req.method === "GET") {
    try {
      const { data, error } = await sb
        .from("topics")
        .select("topic_id, keyword, status, started_at, mapping_hint, auto_daily")
        .eq("workspace_id", caller.workspaceId)
        .order("status", { ascending: true }) // active < archived (alphabetical)
        .order("started_at", { ascending: false });
      if (error) throw error;
      return res.status(200).json({ topics: data ?? [] });
    } catch (e) {
      return failError(res, e);
    }
  }

  if (req.method === "PATCH") {
    // Toggle auto_daily on a topic the caller's workspace owns.
    const topicId = Number(req.body?.topic_id);
    if (!Number.isInteger(topicId) || topicId <= 0) {
      return res.status(400).json({ error: "bad_topic_id" });
    }
    if (typeof req.body?.auto_daily !== "boolean") {
      return res.status(400).json({ error: "bad_auto_daily" });
    }
    try {
      const { data, error } = await sb
        .from("topics")
        .update({ auto_daily: req.body.auto_daily })
        .eq("topic_id", topicId)
        .eq("workspace_id", caller.workspaceId) // scope = authZ: only this workspace's topics
        .select("topic_id, auto_daily");
      if (error) throw error;
      if (!data || data.length === 0) return res.status(404).json({ error: "topic_not_found" });
      return res.status(200).json({ ok: true, topic_id: topicId, auto_daily: data[0].auto_daily });
    } catch (e) {
      return failError(res, e);
    }
  }

  if (req.method === "DELETE") {
    const topicId = Number(req.body?.topic_id);
    if (!Number.isInteger(topicId) || topicId <= 0) {
      return res.status(400).json({ error: "bad_topic_id" });
    }
    try {
      // AuthZ: confirm the topic is in the caller's workspace before the (service-key) cascade RPC,
      // which itself has no workspace check.
      const { data: owned, error: ownErr } = await sb
        .from("topics")
        .select("topic_id")
        .eq("topic_id", topicId)
        .eq("workspace_id", caller.workspaceId)
        .maybeSingle();
      if (ownErr) throw ownErr;
      if (!owned) return res.status(404).json({ error: "topic_not_found" });

      const { data, error } = await sb.rpc("delete_topic_cascade", { p_topic_id: topicId });
      if (error) {
        const msg = error.message || "";
        if (msg.includes("topic_not_found")) {
          return res.status(404).json({ error: "topic_not_found" });
        }
        if (msg.includes("cannot_delete_active_topic")) {
          return res.status(409).json({
            error: "cannot_delete_active",
            message: "The current topic cannot be deleted (switch to another topic first, then delete this one)",
          });
        }
        throw error;
      }
      return res.status(200).json(data);
    } catch (e) {
      return failError(res, e);
    }
  }

  // POST: switch/add this workspace's active topic — single-transaction RPC (archive old active +
  // re-activate/insert target; atomic, never leaves the workspace with 0 active).
  const keyword = String(req.body?.keyword ?? "").trim();
  if (!keyword) return res.status(400).json({ error: "missing_keyword" });
  if (!TOPIC_ALLOWLIST.test(keyword)) {
    return res.status(400).json({
      error: "invalid_keyword",
      message: "Topic must be 1–80 chars of letters, digits, spaces, '_', or '-' only.",
    });
  }
  // Optional mapping hint (option 3, Anna 2026-05-28): user-supplied guidance for the LLM's
  // subreddit-mapping pass. Persisted on the topic row so subsequent runs see it.
  const rawHint = req.body?.hint;
  const hint = typeof rawHint === "string" ? rawHint.trim() : "";
  try {
    const { data, error } = await sb.rpc("switch_workspace_topic", {
      p_keyword: keyword,
      p_workspace_id: caller.workspaceId,
    });
    if (error) throw error;
    // After the switch lands, write the hint (or null to clear) onto the topic. Failure here doesn't
    // roll back the switch — switch is the critical path; hint is best-effort.
    if (data && typeof data === "object" && "topic_id" in data) {
      const topicId = (data as { topic_id: number }).topic_id;
      const { error: hintErr } = await sb
        .from("topics")
        .update({ mapping_hint: hint || null })
        .eq("topic_id", topicId)
        .eq("workspace_id", caller.workspaceId);
      if (hintErr) {
        console.error("[api/topics] hint write failed:", hintErr.message);
      }
    }
    res.status(200).json({ ok: true, topic: data });
  } catch (e) {
    failError(res, e);
  }
}
