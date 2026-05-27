/** /api/star — 收藏(POST)/ 取消收藏(DELETE,软删)。body: { post_id, person? }
 *
 *  软删模型(对齐 schema uq_starred_active partial unique):
 *  - POST:插一条 active star;若已 active(唯一冲突 23505)→ 幂等返回 already。
 *  - DELETE:把该人对该帖的 active star 置 deleted_at(历史保留,可再 star)。 */
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
          return res.status(200).json({ ok: true, already: true }); // 已收藏,幂等
        }
        throw error;
      }
      return res.status(200).json({ ok: true });
    }
    // DELETE = 软删:只软删当前 active 的那条
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
