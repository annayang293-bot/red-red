/** /api/star — star (POST) / unstar (DELETE, soft delete). body: { post_id }
 *
 *  Phase 3: stars belong to the caller's WORKSPACE (shared across members), not a free-text person.
 *  Auth-gated; workspace_id is server-resolved from the JWT (never client-supplied).
 *
 *  Soft-delete model (aligned with the uq_starred_active_ws partial unique on (workspace_id, post_id)):
 *  - POST: insert an active star; if one is already active (unique conflict 23505) → idempotent.
 *  - DELETE: set deleted_at on this workspace's active star for this post (history preserved, re-star
 *    allowed). */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["POST", "DELETE"])) return;
  const caller = await resolveCaller(req);
  if (!caller) return res.status(401).json({ error: "unauthorized" });
  const postId = Number(req.body?.post_id);
  if (!Number.isInteger(postId) || postId <= 0) {
    return res.status(400).json({ error: "bad_post_id" });
  }
  try {
    const sb = getSupabaseAdmin();
    if (req.method === "POST") {
      const { error } = await sb
        .from("starred")
        .insert({ workspace_id: caller.workspaceId, post_id: postId });
      if (error) {
        if (error.code === "23505") {
          return res.status(200).json({ ok: true, already: true }); // already starred — idempotent
        }
        throw error;
      }
      return res.status(200).json({ ok: true });
    }
    // DELETE = soft delete: only this workspace's currently-active star for the post.
    const { error } = await sb
      .from("starred")
      .update({ deleted_at: new Date().toISOString() })
      .eq("workspace_id", caller.workspaceId)
      .eq("post_id", postId)
      .is("deleted_at", null);
    if (error) throw error;
    res.status(200).json({ ok: true });
  } catch (e) {
    failError(res, e);
  }
}
