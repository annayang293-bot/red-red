/** /api/star — star (POST) / unstar (DELETE, soft delete). body: { post_id, person? }
 *
 *  Soft-delete model (aligned with schema's uq_starred_active partial unique):
 *  - POST: insert an active star; if already active (unique conflict 23505) → idempotently return already.
 *  - DELETE: set deleted_at on this person's active star for this post (history preserved, re-star allowed). */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["POST", "DELETE"])) return;
  const person = String(req.body?.person || "anna");
  const postId = Number(req.body?.post_id);
  if (!Number.isInteger(postId) || postId <= 0) {
    return res.status(400).json({ error: "bad_post_id" });
  }
  try {
    const sb = getSupabaseAdmin();
    if (req.method === "POST") {
      const { error } = await sb.from("starred").insert({ person, post_id: postId });
      if (error) {
        if (error.code === "23505") {
          return res.status(200).json({ ok: true, already: true }); // already starred — idempotent
        }
        throw error;
      }
      return res.status(200).json({ ok: true });
    }
    // DELETE = soft delete: only soft-delete the currently active row
    const { error } = await sb
      .from("starred")
      .update({ deleted_at: new Date().toISOString() })
      .eq("person", person)
      .eq("post_id", postId)
      .is("deleted_at", null);
    if (error) throw error;
    res.status(200).json({ ok: true });
  } catch (e) {
    failError(res, e);
  }
}
