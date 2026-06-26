/** GET /api/workspaces — the caller's workspaces (for the switcher) + the active workspace's members.
 *
 *  Returns:
 *    { current: <active workspace id>,
 *      workspaces: [{ id, name, role }],            // every workspace the caller belongs to
 *      members:    [{ user_id, email, role, is_self }] }  // members of the ACTIVE workspace
 *
 *  Active workspace = resolveCaller (respects the x-workspace-id header). All reads use the service
 *  key; the membership gate is resolveCaller. */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["GET"])) return;
  try {
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    const sb = getSupabaseAdmin();

    // 1) Every workspace the caller belongs to (for the switcher).
    const { data: mine, error: mErr } = await sb
      .from("workspace_members")
      .select("role, workspaces(id, name)")
      .eq("user_id", caller.userId);
    if (mErr) throw mErr;
    const workspaces = (mine ?? [])
      .map((row) => {
        const ws = row.workspaces as unknown as { id: string; name: string } | null;
        return ws ? { id: ws.id, name: ws.name, role: row.role as string } : null;
      })
      .filter(Boolean);

    // 2) Members of the ACTIVE workspace (with emails — resolved via the admin API per user id).
    const { data: memberRows, error: memErr } = await sb
      .from("workspace_members")
      .select("user_id, role")
      .eq("workspace_id", caller.workspaceId);
    if (memErr) throw memErr;
    const members = await Promise.all(
      (memberRows ?? []).map(async (m) => {
        let email: string | null = null;
        try {
          const { data } = await sb.auth.admin.getUserById(m.user_id);
          email = data?.user?.email ?? null;
        } catch {
          email = null;
        }
        return { user_id: m.user_id, email, role: m.role as string, is_self: m.user_id === caller.userId };
      }),
    );

    res.status(200).json({ current: caller.workspaceId, role: caller.role, workspaces, members });
  } catch (e) {
    failError(res, e);
  }
}
