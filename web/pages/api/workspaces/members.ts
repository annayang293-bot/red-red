/** /api/workspaces/members — manage members of the caller's ACTIVE workspace (Phase 3 sharing).
 *
 *  POST   { email }    — invite an existing user (by email) into the active workspace as a member.
 *  DELETE { user_id }  — remove a member from the active workspace.
 *
 *  OWNER-ONLY: only the workspace owner can add/remove members (matches 0011's RLS intent). The
 *  active workspace + the caller's role come from resolveCaller (respects x-workspace-id).
 *
 *  v1 limitation: the invitee must have signed in at least once (so their auth user exists). If the
 *  email isn't found we return user_not_found so the UI can say "ask them to log in with Google once,
 *  then invite again." (A pending-invite-before-signup flow is a later enhancement.) */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { ensureMethod, failError } from "@/lib/api";
import { resolveCaller } from "@/lib/auth";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Find an auth user by email (case-insensitive). Paginates defensively for small user bases. */
async function findUserByEmail(
  sb: ReturnType<typeof getSupabaseAdmin>,
  email: string,
): Promise<{ id: string; email: string } | null> {
  const target = email.toLowerCase();
  for (let page = 1; page <= 10; page++) {
    const { data, error } = await sb.auth.admin.listUsers({ page, perPage: 200 });
    if (error) throw error;
    const users = data?.users ?? [];
    const hit = users.find((u) => (u.email ?? "").toLowerCase() === target);
    if (hit) return { id: hit.id, email: hit.email ?? email };
    if (users.length < 200) break; // last page
  }
  return null;
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["POST", "DELETE"])) return;
  try {
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    if (caller.role !== "owner") {
      return res.status(403).json({ error: "owner_only" }); // only the owner manages members
    }
    const sb = getSupabaseAdmin();

    if (req.method === "POST") {
      const email = String(req.body?.email ?? "").trim().toLowerCase();
      if (!email || !EMAIL_RE.test(email)) {
        return res.status(400).json({ error: "bad_email" });
      }
      const target = await findUserByEmail(sb, email);
      if (!target) {
        return res.status(404).json({ error: "user_not_found" });
      }
      if (target.id === caller.userId) {
        return res.status(400).json({ error: "cannot_invite_self" });
      }
      const { error } = await sb
        .from("workspace_members")
        .insert({ workspace_id: caller.workspaceId, user_id: target.id, role: "member" });
      if (error) {
        if (error.code === "23505") {
          return res.status(200).json({ ok: true, already: true, email }); // already a member
        }
        throw error;
      }
      return res.status(200).json({ ok: true, email });
    }

    // DELETE — remove a member (never the owner, never self).
    const userId = String(req.body?.user_id ?? "").trim();
    if (!userId) return res.status(400).json({ error: "bad_user_id" });
    if (userId === caller.userId) {
      return res.status(400).json({ error: "cannot_remove_owner" });
    }
    const { error } = await sb
      .from("workspace_members")
      .delete()
      .eq("workspace_id", caller.workspaceId)
      .eq("user_id", userId)
      .eq("role", "member"); // only members are removable; owner row is protected
    if (error) throw error;
    return res.status(200).json({ ok: true });
  } catch (e) {
    failError(res, e);
  }
}
