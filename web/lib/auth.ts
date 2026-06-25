/** Server-side caller resolution for API routes (System ① BYOK, Phase 3).
 *
 * Shared by every authenticated route: the browser sends `Authorization: Bearer <supabase
 * access_token>`; we verify it server-side (auth.getUser) to get the user, then resolve the
 * workspace they OWN. Data is scoped to that workspace, so this is the single chokepoint that
 * turns "a logged-in user" into "which workspace's data may they touch".
 *
 * Phase 3 assumption: each user owns exactly one workspace (auto-created at signup; enforced by
 * UNIQUE(owner_id) in 0013). Once members can be invited into OTHER workspaces + there's a
 * workspace switcher, this should take a requested workspace_id and validate membership
 * (is_workspace_member) instead of resolving by ownership — revisit at the invite step.
 *
 * Fails CLOSED: any missing header / bad token / lookup error / no workspace → null (caller 401s).
 */
import type { NextApiRequest } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";

export interface Caller {
  userId: string;
  workspaceId: string;
}

/** Verify the caller's JWT and resolve the workspace they own. null = unauthorized / no workspace. */
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
  const { data, error: wsErr } = await sb
    .from("workspaces")
    .select("id")
    .eq("owner_id", user.id)
    .limit(1);
  if (wsErr) {
    console.error("[auth] workspace lookup error:", wsErr.message);
    return null; // fail closed
  }
  const workspaceId = data?.[0]?.id as string | undefined;
  if (!workspaceId) return null;
  return { userId: user.id, workspaceId };
}
