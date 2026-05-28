/** GET /api/starred?person=anna — that person's current starred library (non-soft-deleted, newest first). */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { StarredRow, starredRowsToItems } from "@/lib/report-mapping";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    const person = String(req.query.person || "anna");
    const { data, error } = await getSupabaseAdmin()
      .from("starred")
      .select("star_id, post_id, starred_at, posts_archive(*)")
      .eq("person", person)
      .is("deleted_at", null)
      .order("starred_at", { ascending: false });
    if (error) throw error;
    const items = starredRowsToItems((data ?? []) as unknown as StarredRow[]);
    res.status(200).json({ person, items });
  } catch (e) {
    failError(res, e);
  }
}
