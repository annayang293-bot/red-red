/** /api/apify-token — manage the caller's workspace Apify token (BYOK Phase 1-B).
 *
 *   GET    → { configured, last6, username, validated_at }  (never returns ciphertext/plaintext)
 *   POST   { token } → validate via Apify GET /users/me → AES-256-GCM encrypt → upsert
 *   DELETE → remove the workspace's token row
 *
 * Auth: the browser sends `Authorization: Bearer <supabase access_token>`. We verify it server-side
 * (auth.getUser) to get the user, then resolve the workspace they OWN (token belongs to the owner;
 * Phase 1 = one owned workspace per user). All DB access uses the service-role key, and the
 * apify_credentials table is server-only (RLS + REVOKE), so the secret never touches the browser.
 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { encryptToken } from "@/lib/crypto";

const APIFY_API = "https://api.apify.com/v2";

/** Confirm the token is live by calling Apify (Bearer header — never the token in the URL/query). */
async function validateApifyToken(
  token: string,
): Promise<{ ok: boolean; username?: string }> {
  try {
    const r = await fetch(`${APIFY_API}/users/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) return { ok: false };
    const j = (await r.json()) as { data?: { username?: string } };
    return { ok: true, username: j?.data?.username };
  } catch {
    return { ok: false };
  }
}

/** Verify the caller's JWT and resolve the workspace they own. null = unauthorized / no workspace. */
async function resolveCaller(
  req: NextApiRequest,
): Promise<{ userId: string; workspaceId: string } | null> {
  const auth = req.headers.authorization;
  if (!auth?.startsWith("Bearer ")) return null;
  const accessToken = auth.slice("Bearer ".length);
  const sb = getSupabaseAdmin();
  const {
    data: { user },
    error,
  } = await sb.auth.getUser(accessToken);
  if (error || !user) return null;
  const { data } = await sb
    .from("workspaces")
    .select("id")
    .eq("owner_id", user.id)
    .order("created_at", { ascending: true })
    .limit(1);
  const workspaceId = data?.[0]?.id as string | undefined;
  if (!workspaceId) return null;
  return { userId: user.id, workspaceId };
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  const caller = await resolveCaller(req);
  if (!caller) return res.status(401).json({ error: "unauthorized" });
  const sb = getSupabaseAdmin();

  if (req.method === "GET") {
    const { data } = await sb
      .from("apify_credentials")
      .select("token_last6, account_username, validated_at")
      .eq("workspace_id", caller.workspaceId)
      .maybeSingle();
    return res.status(200).json({
      configured: Boolean(data),
      last6: data?.token_last6 ?? null,
      username: data?.account_username ?? null,
      validated_at: data?.validated_at ?? null,
    });
  }

  if (req.method === "POST") {
    const token = String(req.body?.token ?? "").trim();
    if (!token) return res.status(400).json({ error: "missing_token" });

    const v = await validateApifyToken(token);
    if (!v.ok) {
      return res.status(400).json({
        error: "invalid_token",
        message: "这个 Apify token 无效或没权限(Apify GET /users/me 没通过)。",
      });
    }

    const enc = encryptToken(token, caller.workspaceId); // AAD = workspace_id
    const { error } = await sb.from("apify_credentials").upsert(
      {
        workspace_id: caller.workspaceId,
        ciphertext: enc.ciphertext,
        nonce: enc.nonce,
        auth_tag: enc.authTag,
        key_version: 1,
        token_last6: token.slice(-6),
        account_username: v.username ?? null,
        validated_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      { onConflict: "workspace_id" },
    );
    if (error) return res.status(500).json({ error: "store_failed", message: error.message });
    return res.status(200).json({ ok: true, last6: token.slice(-6), username: v.username ?? null });
  }

  if (req.method === "DELETE") {
    const { error } = await sb
      .from("apify_credentials")
      .delete()
      .eq("workspace_id", caller.workspaceId);
    if (error) return res.status(500).json({ error: "delete_failed", message: error.message });
    return res.status(200).json({ ok: true });
  }

  res.setHeader("Allow", "GET, POST, DELETE");
  return res.status(405).json({ error: "method_not_allowed" });
}
