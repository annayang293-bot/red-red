/** Server-side caller resolution for API routes (System ① BYOK, Phase 3).
 *
 * Shared by every authenticated route: the browser sends `Authorization: Bearer <supabase
 * access_token>`; we verify it server-side (auth.getUser) to get the user, then resolve WHICH
 * workspace this request acts in and scope all data to it.
 *
 * Multi-workspace (Phase 3 sharing): a user belongs to one or more workspaces via
 * `workspace_members` (their own auto-created one with role='owner', plus any they were invited into
 * with role='member'). The browser picks the active workspace and sends it as the `x-workspace-id`
 * header (from the workspace switcher). We validate membership of that id. With no header (a single-
 * workspace user, or first load) we default to the workspace they OWN, else their first membership —
 * so existing single-workspace behavior is unchanged.
 *
 * Fails CLOSED: missing header → null; bad token → null; requested a workspace they're NOT a member
 * of → null (caller 401s). The membership check is the cross-workspace access boundary.
 */
import type { NextApiRequest } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";

export interface Caller {
  userId: string;
  workspaceId: string;
  role: string; // 'owner' | 'member' — for the active workspace
}

/** Verify the caller's JWT and resolve the active workspace (membership-checked). null = unauthorized. */
export async function resolveCaller(req: NextApiRequest): Promise<Caller | null> {
  const auth = req.headers.authorization;
  if (!auth?.startsWith("Bearer ")) return null;
  const accessToken = auth.slice("Bearer ".length);
  const sb = getSupabaseAdmin();
  const {
    data: { user },
    error,
  } = await sb.auth.getUser(accessToken);
  if (error || !user) return null;

  const { data: memberships, error: mErr } = await sb
    .from("workspace_members")
    .select("workspace_id, role")
    .eq("user_id", user.id);
  if (mErr) {
    console.error("[auth] membership lookup error:", mErr.message);
    return null; // fail closed
  }
  if (!memberships || memberships.length === 0) return null;

  const requestedRaw = req.headers["x-workspace-id"];
  const requested = Array.isArray(requestedRaw) ? requestedRaw[0] : requestedRaw;

  let chosen: { workspace_id: string; role: string } | undefined;
  if (requested) {
    // Acting in a specific workspace: must be a member of it (cross-workspace access boundary).
    chosen = memberships.find((m) => m.workspace_id === requested);
    if (!chosen) return null;
  } else {
    // Default: the workspace they own; otherwise the first one they belong to.
    chosen = memberships.find((m) => m.role === "owner") ?? memberships[0];
  }
  return { userId: user.id, workspaceId: chosen.workspace_id, role: chosen.role };
}
