/** GET /api/starred — the caller's WORKSPACE starred library (Phase 3): shared across the
 *  workspace's members, non-soft-deleted, newest first. Auth-gated + workspace-scoped. */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";
import { StarredRow, starredRowsToItems } from "@/lib/report-mapping";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    const { data, error } = await getSupabaseAdmin()
      .from("starred")
      .select("star_id, post_id, starred_at, posts_archive(*)")
      .eq("workspace_id", caller.workspaceId)
      .is("deleted_at", null)
      .order("starred_at", { ascending: false });
    if (error) throw error;
    const items = starredRowsToItems((data ?? []) as unknown as StarredRow[]);
    res.status(200).json({ items });
  } catch (e) {
    failError(res, e);
  }
}
